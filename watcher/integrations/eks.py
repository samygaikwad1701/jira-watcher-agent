"""EKS / kubectl connectivity.

No KB skill exists — this shells out to `aws` and `kubectl` directly. Verify
is read-only (STS + describe-cluster + optional kubectl probe). Writing the
kubeconfig entry is a separate opt-in action (`watcher --eks-login`) since
it mutates `~/.kube/config`.

Prerequisites the user must handle themselves:
- `aws` and `kubectl` on PATH.
- Valid AWS creds — usually `aws sso login` for SSO orgs.
- The IAM principal must be mapped in the cluster's `aws-auth` ConfigMap
  (or in EKS access entries).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Optional

from ._base import IntegrationStatus


class EksClient:
    """AWS EKS + kubectl helper.

    All probing calls are strictly read-only (`kubectl get`) — the client
    intentionally exposes NO write/apply/delete primitives. The only
    mutating operation is `update_kubeconfig()`, and that is gated behind
    the `--eks-login` CLI flag (never called during investigation).
    """

    def __init__(
        self,
        cluster_name: str,
        region: str,
        profile: Optional[str] = None,
        namespace: str = "default",
        kube_context: Optional[str] = None,
        timeout: int = 25,
    ):
        self.cluster_name = cluster_name
        self.region = region
        self.profile = profile
        self.namespace = namespace
        self.kube_context = kube_context
        self.timeout = timeout

    # ---------- helpers ----------

    def _env(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.profile:
            env["AWS_PROFILE"] = self.profile
        return env

    def _run(self, cmd: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=self._env(),
        )

    def _kubectl(self, args: list[str]) -> subprocess.CompletedProcess:
        """Read-only kubectl invocation, scoped to the selected context.

        Refuses any subcommand that isn't `get` or `config` — a belt-and-
        braces guard so a future edit can't accidentally make write calls
        against a production cluster.
        """
        if not args:
            raise ValueError("kubectl call requires at least one arg")
        head = args[0]
        if head not in {"get", "config", "describe"}:
            raise ValueError(
                f"read-only kubectl guard: subcommand {head!r} is not allowed during investigation"
            )
        cmd = ["kubectl"]
        if self.kube_context:
            cmd += ["--context", self.kube_context]
        cmd += args
        return self._run(cmd)

    # ---------- context selection ----------

    def list_contexts(self) -> list[str]:
        """Return kubectl contexts on this machine (empty on any failure)."""
        if not shutil.which("kubectl"):
            return []
        result = self._run(["kubectl", "config", "get-contexts", "-o", "name"])
        if result.returncode != 0:
            return []
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def current_context(self) -> Optional[str]:
        if not shutil.which("kubectl"):
            return None
        result = self._run(["kubectl", "config", "current-context"])
        if result.returncode != 0:
            return None
        return (result.stdout or "").strip() or None

    def set_kube_context(self, name: Optional[str]) -> None:
        """Set the context used for subsequent probing calls. Does NOT run
        `kubectl config use-context` — we pass `--context` per call to avoid
        mutating the user's kubeconfig state."""
        self.kube_context = name or None

    def _require_binaries(self, *binaries: str) -> Optional[IntegrationStatus]:
        for binary in binaries:
            if not shutil.which(binary):
                return IntegrationStatus(
                    name="eks",
                    ok=False,
                    error=f"{binary!r} not on PATH",
                    hint=(
                        "Install both `aws` (AWS CLI v2) and `kubectl` before running EKS checks."
                    ),
                )
        return None

    # ---------- verify ----------

    def verify(self) -> IntegrationStatus:
        missing = self._require_binaries("aws", "kubectl")
        if missing:
            return missing

        result = self._run(["aws", "sts", "get-caller-identity", "--output", "json"])
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:300]
            return IntegrationStatus(
                name="eks",
                ok=False,
                error=f"aws sts failed: {stderr}",
                hint="If this is an SSO account, run `aws sso login` first.",
            )
        try:
            identity = json.loads(result.stdout)
        except json.JSONDecodeError:
            identity = {}
        arn = identity.get("Arn", "")

        result = self._run(
            [
                "aws",
                "eks",
                "describe-cluster",
                "--name",
                self.cluster_name,
                "--region",
                self.region,
                "--query",
                "cluster.status",
                "--output",
                "text",
            ]
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:300]
            return IntegrationStatus(
                name="eks",
                ok=False,
                identity=arn,
                error=f"describe-cluster failed: {stderr}",
                hint=(
                    f"Confirm cluster {self.cluster_name!r} exists in region "
                    f"{self.region!r} and that your IAM principal has "
                    "eks:DescribeCluster permission."
                ),
            )
        status = (result.stdout or "").strip()

        return IntegrationStatus(
            name="eks",
            ok=(status == "ACTIVE"),
            identity=arn,
            details={
                "cluster": self.cluster_name,
                "region": self.region,
                "status": status,
                "profile": self.profile or "<default>",
            },
            error=None if status == "ACTIVE" else f"cluster status = {status!r}",
        )

    # ---------- optional: write kubeconfig ----------

    def update_kubeconfig(self) -> IntegrationStatus:
        missing = self._require_binaries("aws")
        if missing:
            return missing
        result = self._run(
            [
                "aws",
                "eks",
                "update-kubeconfig",
                "--name",
                self.cluster_name,
                "--region",
                self.region,
            ]
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()[:300]
            return IntegrationStatus(
                name="eks",
                ok=False,
                error=f"update-kubeconfig failed: {stderr}",
            )
        return IntegrationStatus(
            name="eks",
            ok=True,
            details={
                "cluster": self.cluster_name,
                "region": self.region,
                "message": (result.stdout or "").strip(),
            },
        )

    # ---------- workload probing (READ ONLY) ----------

    def probe_workload(self, name: str, namespace: Optional[str] = None) -> dict:
        """Read-only probe of a deployment + its pods + recent events.

        Uses `kubectl get` exclusively. Returns a dict suitable for injection
        into the investigator prompt. Missing resources return `{"found": False}`;
        all errors are captured, never raised — the investigator treats absent
        evidence as "no signal", not as failure.
        """
        ns = namespace or self.namespace
        if not shutil.which("kubectl"):
            return {"found": False, "error": "kubectl not on PATH"}

        dep = self._kubectl(["get", "deployment", name, "-n", ns, "-o", "json"])
        if dep.returncode != 0:
            stderr = (dep.stderr or "").strip().lower()
            if "notfound" in stderr or "not found" in stderr:
                return {
                    "found": False,
                    "namespace": ns,
                    "name": name,
                    "context": self.kube_context,
                }
            return {
                "found": False,
                "namespace": ns,
                "name": name,
                "context": self.kube_context,
                "error": (dep.stderr or "").strip()[:200],
            }
        try:
            data = json.loads(dep.stdout)
        except json.JSONDecodeError:
            return {
                "found": False,
                "namespace": ns,
                "name": name,
                "context": self.kube_context,
                "error": "non-JSON deployment",
            }

        status = data.get("status") or {}
        spec = data.get("spec") or {}
        conditions = status.get("conditions") or []
        available = next((c for c in conditions if c.get("type") == "Available"), {}) or {}

        pods = self._kubectl(["get", "pods", "-n", ns, "-l", f"app={name}", "-o", "json"])
        pod_summary: list[dict] = []
        if pods.returncode == 0:
            try:
                pod_data = json.loads(pods.stdout) or {}
            except json.JSONDecodeError:
                pod_data = {}
            for item in (pod_data.get("items") or [])[:5]:
                pmeta = item.get("metadata") or {}
                pstatus = item.get("status") or {}
                container_statuses = pstatus.get("containerStatuses") or []
                restart_count = sum(int(c.get("restartCount", 0)) for c in container_statuses)
                waiting_reasons = [
                    ((c.get("state") or {}).get("waiting") or {}).get("reason")
                    for c in container_statuses
                ]
                pod_summary.append(
                    {
                        "name": pmeta.get("name"),
                        "phase": pstatus.get("phase"),
                        "ready": all(c.get("ready") for c in container_statuses)
                        if container_statuses
                        else False,
                        "restarts": restart_count,
                        "waiting": [r for r in waiting_reasons if r],
                    }
                )

        events = self._kubectl(
            [
                "get",
                "events",
                "-n",
                ns,
                "--field-selector",
                f"involvedObject.name={name}",
                "--sort-by=.lastTimestamp",
                "-o",
                "json",
            ]
        )
        event_summary: list[dict] = []
        if events.returncode == 0:
            try:
                ev_data = json.loads(events.stdout) or {}
            except json.JSONDecodeError:
                ev_data = {}
            for item in (ev_data.get("items") or [])[-5:]:
                event_summary.append(
                    {
                        "type": item.get("type"),
                        "reason": item.get("reason"),
                        "message": (item.get("message") or "").strip()[:200],
                        "count": item.get("count", 1),
                        "last": item.get("lastTimestamp"),
                    }
                )

        return {
            "found": True,
            "namespace": ns,
            "name": name,
            "context": self.kube_context,
            "replicas_desired": spec.get("replicas"),
            "replicas_ready": status.get("readyReplicas") or 0,
            "replicas_available": status.get("availableReplicas") or 0,
            "available_condition": {
                "status": available.get("status"),
                "reason": available.get("reason"),
                "last_transition": available.get("lastTransitionTime"),
            },
            "pods": pod_summary,
            "events": event_summary,
        }


def from_env() -> Optional[EksClient]:
    cluster = (os.environ.get("EKS_CLUSTER_NAME") or "").strip()
    region = (os.environ.get("EKS_REGION") or os.environ.get("AWS_REGION") or "").strip()
    if not cluster or not region:
        return None
    return EksClient(
        cluster_name=cluster,
        region=region,
        profile=(os.environ.get("AWS_PROFILE") or "").strip() or None,
        namespace=(os.environ.get("EKS_NAMESPACE") or "default").strip() or "default",
        kube_context=(os.environ.get("KUBE_CONTEXT") or "").strip() or None,
    )
