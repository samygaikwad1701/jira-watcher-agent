"""GitHub connectivity.

No KB skill exists — this mirrors the Bitbucket pattern. Prefers the local
`gh` CLI when it's installed and authenticated (no token juggling); falls
back to a bearer PAT via `GITHUB_TOKEN`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import Optional

import requests

from ._base import IntegrationStatus

USER_AGENT = "watcher-github/0.1"


class GitHubClient:
    def __init__(
        self,
        token: Optional[str] = None,
        use_gh: bool = False,
        api_base: str = "https://api.github.com",
        timeout: int = 15,
    ):
        self.token = token
        self.use_gh = use_gh
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "User-Agent": USER_AGENT,
                "X-GitHub-Api-Version": "2022-11-28",
            }
        )
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def _verify_gh(self) -> IntegrationStatus:
        try:
            result = subprocess.run(
                ["gh", "api", "/user"],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()[:200]
            return IntegrationStatus(
                name="github",
                ok=False,
                error=f"gh CLI exited {exc.returncode}: {stderr or '<no stderr>'}",
                hint="Run `gh auth login` and retry.",
            )
        except FileNotFoundError:
            return IntegrationStatus(name="github", ok=False, error="gh CLI not on PATH")
        except subprocess.TimeoutExpired:
            return IntegrationStatus(name="github", ok=False, error="gh api /user timed out")
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return IntegrationStatus(
                name="github",
                ok=False,
                error=f"gh returned non-JSON: {result.stdout[:200]}",
            )
        return IntegrationStatus(
            name="github",
            ok=True,
            identity=str(data.get("login") or "?"),
            details={"via": "gh CLI", "name": data.get("name")},
        )

    def _verify_rest(self) -> IntegrationStatus:
        if not self.token:
            return IntegrationStatus(
                name="github",
                ok=False,
                error="GITHUB_TOKEN not set and gh CLI unavailable",
                hint="Create a PAT at https://github.com/settings/tokens or install `gh`.",
            )
        try:
            resp = self.session.get(f"{self.api_base}/user", timeout=self.timeout)
        except requests.RequestException as exc:
            return IntegrationStatus(name="github", ok=False, error=f"network error: {exc}")
        if resp.status_code >= 400:
            return IntegrationStatus(
                name="github",
                ok=False,
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                hint=(
                    "Token needs at least `read:user` scope. For private-repo access, add `repo`."
                ),
            )
        data = resp.json()
        return IntegrationStatus(
            name="github",
            ok=True,
            identity=str(data.get("login") or "?"),
            details={"via": "REST API", "name": data.get("name")},
        )

    def verify(self) -> IntegrationStatus:
        if self.use_gh:
            return self._verify_gh()
        return self._verify_rest()


def from_env() -> Optional[GitHubClient]:
    token = (os.environ.get("GITHUB_TOKEN") or "").strip() or None
    prefer_gh = (os.environ.get("GITHUB_USE_GH", "") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    has_gh = shutil.which("gh") is not None

    if not token and not has_gh:
        return None
    if prefer_gh and has_gh:
        return GitHubClient(token=token, use_gh=True)
    if token:
        return GitHubClient(token=token, use_gh=False)
    if has_gh:
        return GitHubClient(token=None, use_gh=True)
    return None
