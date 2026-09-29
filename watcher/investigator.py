"""Resolve stage: pull a ticket's full context (description, comments, remote
links, and linked PR metadata when possible) and ask the LLM whether the
reported problem is real, plus concrete resolution paths.

This is deliberately per-ticket rather than batched:

- Investigation prompts are larger than digest prompts and don't benefit
  from packing multiple tickets together.
- One failure shouldn't wipe out the rest of the run.
- Rendering a panel-per-ticket is cleaner and streams naturally.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

import requests

from .jira_client import JiraClient, Ticket
from .memory import Memory
from .providers import ProviderError, run_provider


# ---------- URL extraction ----------

BITBUCKET_CLOUD_PR_RE = re.compile(
    r"https?://bitbucket\.org/([\w.-]+)/([\w.-]+)/pull-requests/(\d+)",
    re.IGNORECASE,
)
BITBUCKET_DC_PR_RE = re.compile(
    r"https?://[\w.-]+/projects/([\w.-]+)/repos/([\w.-]+)/pull-requests/(\d+)",
    re.IGNORECASE,
)
GITHUB_PR_RE = re.compile(
    r"https?://github\.com/([\w.-]+)/([\w.-]+)/pull/(\d+)",
    re.IGNORECASE,
)
# Repo URLs (any bitbucket.org/<ws>/<repo> — with or without a trailing path).
# We deliberately don't require an anchored end; we just capture the first two
# path segments and later dedupe against PR refs for the same (ws, repo).
BITBUCKET_CLOUD_REPO_RE = re.compile(
    r"https?://bitbucket\.org/([\w.-]+)/([\w.-]+?)(?=/|\s|$|[\)\]\"'>])",
    re.IGNORECASE,
)
BITBUCKET_DC_REPO_RE = re.compile(
    r"https?://[\w.-]+/projects/([\w.-]+)/repos/([\w.-]+?)(?=/|\s|$|[\)\]\"'>])",
    re.IGNORECASE,
)
GITHUB_REPO_RE = re.compile(
    r"https?://github\.com/([\w.-]+)/([\w.-]+?)(?=/|\s|$|[\)\]\"'>])",
    re.IGNORECASE,
)
# Jenkins jobs — captures host base + the /job/… path (supports nested folders).
JENKINS_JOB_RE = re.compile(
    r"(https?://[\w.-]+(?::\d+)?)(/job/[\w./%-]+?)(?:/(\d+)/?)?(?=/|\s|$|[\)\]\"'>])",
    re.IGNORECASE,
)
# Reserved first-segment paths on Bitbucket / GitHub that aren't real repos.
_BB_RESERVED = {"workspaces", "account", "repo", "dashboard", "settings", "repos", "snippets"}
_GH_RESERVED = {
    "orgs", "settings", "notifications", "issues", "pulls", "features",
    "explore", "marketplace", "topics", "trending", "collections", "events",
    "login", "logout", "signup", "search", "about",
}


@dataclass
class PRRef:
    provider: str            # "bitbucket-cloud" | "bitbucket-dc" | "github"
    owner_or_workspace: str  # workspace (BB Cloud) / project (BB DC) / org (GH)
    repo: str
    number: str
    url: str


@dataclass
class PRSummary:
    ref: PRRef
    title: str = ""
    state: str = ""
    author: str = ""
    updated: str = ""
    fetched: bool = False
    error: str = ""


@dataclass
class RepoRef:
    provider: str            # "bitbucket-cloud" | "bitbucket-dc" | "github"
    owner_or_workspace: str
    repo: str
    url: str


@dataclass
class RepoSummary:
    ref: RepoRef
    exists: bool = False
    last_activity: str = ""
    default_branch: str = ""
    is_private: bool = False
    fetched: bool = False
    error: str = ""


@dataclass
class JenkinsJobRef:
    base_url: str            # scheme://host[:port]
    job_path: str            # /job/foo[/job/bar]
    build_number: Optional[str]
    url: str


@dataclass
class JenkinsJobSummary:
    ref: JenkinsJobRef
    exists: bool = False
    disabled: bool = False
    last_build_number: Optional[int] = None
    last_build_status: str = ""  # SUCCESS / FAILURE / UNSTABLE / ABORTED / ""
    last_build_at: str = ""      # ISO timestamp
    fetched: bool = False
    error: str = ""


@dataclass
class EnrichedTicket:
    ticket: Ticket
    remote_links: list[dict[str, Any]] = field(default_factory=list)
    prs: list[PRSummary] = field(default_factory=list)
    repos: list[RepoSummary] = field(default_factory=list)
    jenkins_jobs: list[JenkinsJobSummary] = field(default_factory=list)
    other_links: list[str] = field(default_factory=list)
    workload_probes: list[dict[str, Any]] = field(default_factory=list)


def _iter_text_sources(t: Ticket, remote_links: list[dict[str, Any]]) -> Iterable[str]:
    yield t.summary or ""
    yield t.description or ""
    for c in t.comments:
        yield c.body or ""
    for link in remote_links:
        obj = link.get("object") or {}
        url = obj.get("url")
        if isinstance(url, str):
            yield url


def _extract_pr_refs(text: str) -> list[PRRef]:
    refs: list[PRRef] = []
    for m in BITBUCKET_CLOUD_PR_RE.finditer(text or ""):
        refs.append(PRRef("bitbucket-cloud", m.group(1), m.group(2), m.group(3), m.group(0)))
    for m in BITBUCKET_DC_PR_RE.finditer(text or ""):
        if "bitbucket.org" in m.group(0):
            continue
        refs.append(PRRef("bitbucket-dc", m.group(1), m.group(2), m.group(3), m.group(0)))
    for m in GITHUB_PR_RE.finditer(text or ""):
        refs.append(PRRef("github", m.group(1), m.group(2), m.group(3), m.group(0)))
    return refs


def _collect_pr_refs(t: Ticket, remote_links: list[dict[str, Any]]) -> list[PRRef]:
    seen: set[tuple[str, str, str, str]] = set()
    out: list[PRRef] = []
    for source in _iter_text_sources(t, remote_links):
        for ref in _extract_pr_refs(source):
            key = (ref.provider, ref.owner_or_workspace, ref.repo, ref.number)
            if key in seen:
                continue
            seen.add(key)
            out.append(ref)
    return out


def _extract_repo_refs(text: str) -> list[RepoRef]:
    """Match Bitbucket / GitHub repo URLs, skipping obvious non-repo path segments."""
    refs: list[RepoRef] = []
    for m in BITBUCKET_CLOUD_REPO_RE.finditer(text or ""):
        ws, repo = m.group(1), m.group(2)
        if ws.lower() in _BB_RESERVED:
            continue
        # Skip any URL that has /pull-requests/ downstream — those go through
        # the PR path and imply the repo exists.
        if "/pull-requests/" in m.group(0):
            continue
        refs.append(RepoRef("bitbucket-cloud", ws, repo, m.group(0)))
    for m in BITBUCKET_DC_REPO_RE.finditer(text or ""):
        if "bitbucket.org" in m.group(0):
            continue
        if "/pull-requests/" in m.group(0):
            continue
        refs.append(RepoRef("bitbucket-dc", m.group(1), m.group(2), m.group(0)))
    for m in GITHUB_REPO_RE.finditer(text or ""):
        owner, repo = m.group(1), m.group(2)
        if owner.lower() in _GH_RESERVED:
            continue
        if "/pull/" in m.group(0) or "/issues/" in m.group(0):
            continue
        refs.append(RepoRef("github", owner, repo, m.group(0)))
    return refs


def _collect_repo_refs(
    t: Ticket, remote_links: list[dict[str, Any]], pr_refs: list[PRRef]
) -> list[RepoRef]:
    """Repos mentioned in the ticket, deduped against PR refs for the same repo."""
    already = {(p.provider, p.owner_or_workspace.lower(), p.repo.lower()) for p in pr_refs}
    seen: set[tuple[str, str, str]] = set()
    out: list[RepoRef] = []
    for source in _iter_text_sources(t, remote_links):
        for ref in _extract_repo_refs(source):
            key = (ref.provider, ref.owner_or_workspace.lower(), ref.repo.lower())
            if key in seen or key in already:
                continue
            seen.add(key)
            out.append(ref)
    return out


def _extract_jenkins_refs(text: str) -> list[JenkinsJobRef]:
    out: list[JenkinsJobRef] = []
    for m in JENKINS_JOB_RE.finditer(text or ""):
        base, path, build = m.group(1), m.group(2), m.group(3)
        out.append(
            JenkinsJobRef(
                base_url=base.rstrip("/"),
                job_path=path.rstrip("/"),
                build_number=build,
                url=m.group(0).rstrip("/"),
            )
        )
    return out


def _collect_jenkins_refs(
    t: Ticket, remote_links: list[dict[str, Any]]
) -> list[JenkinsJobRef]:
    seen: set[tuple[str, str]] = set()
    out: list[JenkinsJobRef] = []
    for source in _iter_text_sources(t, remote_links):
        for ref in _extract_jenkins_refs(source):
            key = (ref.base_url, ref.job_path)
            if key in seen:
                continue
            seen.add(key)
            out.append(ref)
    return out


def _collect_other_links(
    t: Ticket,
    remote_links: list[dict[str, Any]],
    already_seen_urls: set[str],
) -> list[str]:
    """Anything else worth mentioning to the LLM (titled remote links etc.)."""
    seen: set[str] = set(already_seen_urls)
    out: list[str] = []
    for link in remote_links:
        obj = link.get("object") or {}
        url = obj.get("url")
        title = obj.get("title") or ""
        if not isinstance(url, str) or url in seen:
            continue
        # Skip if it's something we already probed.
        if _extract_pr_refs(url) or _extract_repo_refs(url) or _extract_jenkins_refs(url):
            continue
        seen.add(url)
        out.append(f"{url}  ({title})" if title else url)
    return out


# ---------- PR fetching ----------

def _fetch_bitbucket_cloud_pr(bb_client, ref: PRRef) -> PRSummary:
    api = bb_client.api_base
    url = f"{api}/repositories/{ref.owner_or_workspace}/{ref.repo}/pullrequests/{ref.number}"
    try:
        resp = bb_client.session.get(url, auth=bb_client.auth, timeout=bb_client.timeout)
    except requests.RequestException as exc:
        return PRSummary(ref=ref, error=f"network error: {exc}")
    if resp.status_code >= 400:
        return PRSummary(ref=ref, error=f"HTTP {resp.status_code}")
    data = resp.json() or {}
    return PRSummary(
        ref=ref,
        title=str(data.get("title", "")),
        state=str(data.get("state", "")),
        author=str((data.get("author") or {}).get("display_name") or ""),
        updated=str(data.get("updated_on", "")),
        fetched=True,
    )


def _fetch_bitbucket_dc_pr(bb_client, ref: PRRef) -> PRSummary:
    api = bb_client.api_base  # e.g. https://<host>/rest/api/1.0
    url = f"{api}/projects/{ref.owner_or_workspace}/repos/{ref.repo}/pull-requests/{ref.number}"
    try:
        resp = bb_client.session.get(url, auth=bb_client.auth, timeout=bb_client.timeout)
    except requests.RequestException as exc:
        return PRSummary(ref=ref, error=f"network error: {exc}")
    if resp.status_code >= 400:
        return PRSummary(ref=ref, error=f"HTTP {resp.status_code}")
    data = resp.json() or {}
    return PRSummary(
        ref=ref,
        title=str(data.get("title", "")),
        state=str(data.get("state", "")),
        author=str((data.get("author") or {}).get("user", {}).get("displayName") or ""),
        updated=str(data.get("updatedDate", "")),
        fetched=True,
    )


def _fetch_github_pr(gh_client, ref: PRRef) -> PRSummary:
    url = f"{gh_client.api_base}/repos/{ref.owner_or_workspace}/{ref.repo}/pulls/{ref.number}"
    try:
        if gh_client.use_gh:
            # Use `gh api` so we inherit the CLI's auth.
            result = subprocess.run(
                ["gh", "api", f"/repos/{ref.owner_or_workspace}/{ref.repo}/pulls/{ref.number}"],
                capture_output=True, text=True, timeout=gh_client.timeout, check=True,
            )
            data = json.loads(result.stdout) or {}
        else:
            resp = gh_client.session.get(url, timeout=gh_client.timeout)
            if resp.status_code >= 400:
                return PRSummary(ref=ref, error=f"HTTP {resp.status_code}")
            data = resp.json() or {}
    except subprocess.CalledProcessError as exc:
        return PRSummary(ref=ref, error=f"gh api exit {exc.returncode}")
    except requests.RequestException as exc:
        return PRSummary(ref=ref, error=f"network error: {exc}")
    return PRSummary(
        ref=ref,
        title=str(data.get("title", "")),
        state=str(data.get("state", "")),
        author=str((data.get("user") or {}).get("login") or ""),
        updated=str(data.get("updated_at", "")),
        fetched=True,
    )


def _fetch_pr(bb_client, gh_client, ref: PRRef) -> PRSummary:
    if ref.provider == "bitbucket-cloud":
        if bb_client is None or not bb_client.is_cloud:
            return PRSummary(ref=ref, error="Bitbucket Cloud not configured")
        return _fetch_bitbucket_cloud_pr(bb_client, ref)
    if ref.provider == "bitbucket-dc":
        if bb_client is None or bb_client.is_cloud:
            return PRSummary(ref=ref, error="Bitbucket DC not configured")
        return _fetch_bitbucket_dc_pr(bb_client, ref)
    if ref.provider == "github":
        if gh_client is None:
            return PRSummary(ref=ref, error="GitHub not configured")
        return _fetch_github_pr(gh_client, ref)
    return PRSummary(ref=ref, error=f"unknown provider {ref.provider}")


# ---------- repo existence fetching ----------

def _fetch_bitbucket_cloud_repo(bb_client, ref: RepoRef) -> RepoSummary:
    url = f"{bb_client.api_base}/repositories/{ref.owner_or_workspace}/{ref.repo}"
    try:
        resp = bb_client.session.get(url, auth=bb_client.auth, timeout=bb_client.timeout)
    except requests.RequestException as exc:
        return RepoSummary(ref=ref, error=f"network error: {exc}")
    if resp.status_code == 404:
        return RepoSummary(ref=ref, exists=False, fetched=True)
    if resp.status_code >= 400:
        return RepoSummary(ref=ref, error=f"HTTP {resp.status_code}")
    data = resp.json() or {}
    return RepoSummary(
        ref=ref,
        exists=True,
        fetched=True,
        last_activity=str(data.get("updated_on", "")),
        default_branch=str((data.get("mainbranch") or {}).get("name") or ""),
        is_private=bool(data.get("is_private", False)),
    )


def _fetch_bitbucket_dc_repo(bb_client, ref: RepoRef) -> RepoSummary:
    url = f"{bb_client.api_base}/projects/{ref.owner_or_workspace}/repos/{ref.repo}"
    try:
        resp = bb_client.session.get(url, auth=bb_client.auth, timeout=bb_client.timeout)
    except requests.RequestException as exc:
        return RepoSummary(ref=ref, error=f"network error: {exc}")
    if resp.status_code == 404:
        return RepoSummary(ref=ref, exists=False, fetched=True)
    if resp.status_code >= 400:
        return RepoSummary(ref=ref, error=f"HTTP {resp.status_code}")
    data = resp.json() or {}
    return RepoSummary(
        ref=ref,
        exists=True,
        fetched=True,
        default_branch=str((data.get("defaultBranch") or "").replace("refs/heads/", "")),
        is_private=not bool(data.get("public", False)),
    )


def _fetch_github_repo(gh_client, ref: RepoRef) -> RepoSummary:
    path = f"/repos/{ref.owner_or_workspace}/{ref.repo}"
    try:
        if gh_client.use_gh:
            result = subprocess.run(
                ["gh", "api", path],
                capture_output=True, text=True, timeout=gh_client.timeout, check=False,
            )
            if result.returncode != 0:
                stderr = (result.stderr or "").lower()
                if "not found" in stderr or "http 404" in stderr:
                    return RepoSummary(ref=ref, exists=False, fetched=True)
                return RepoSummary(ref=ref, error=(result.stderr or "").strip()[:200])
            data = json.loads(result.stdout) or {}
        else:
            resp = gh_client.session.get(
                f"{gh_client.api_base}{path}", timeout=gh_client.timeout
            )
            if resp.status_code == 404:
                return RepoSummary(ref=ref, exists=False, fetched=True)
            if resp.status_code >= 400:
                return RepoSummary(ref=ref, error=f"HTTP {resp.status_code}")
            data = resp.json() or {}
    except subprocess.CalledProcessError as exc:
        return RepoSummary(ref=ref, error=f"gh api exit {exc.returncode}")
    except requests.RequestException as exc:
        return RepoSummary(ref=ref, error=f"network error: {exc}")
    return RepoSummary(
        ref=ref,
        exists=True,
        fetched=True,
        last_activity=str(data.get("pushed_at", "")),
        default_branch=str(data.get("default_branch", "")),
        is_private=bool(data.get("private", False)),
    )


def _fetch_repo(bb_client, gh_client, ref: RepoRef) -> RepoSummary:
    if ref.provider == "bitbucket-cloud":
        if bb_client is None or not bb_client.is_cloud:
            return RepoSummary(ref=ref, error="Bitbucket Cloud not configured")
        return _fetch_bitbucket_cloud_repo(bb_client, ref)
    if ref.provider == "bitbucket-dc":
        if bb_client is None or bb_client.is_cloud:
            return RepoSummary(ref=ref, error="Bitbucket DC not configured")
        return _fetch_bitbucket_dc_repo(bb_client, ref)
    if ref.provider == "github":
        if gh_client is None:
            return RepoSummary(ref=ref, error="GitHub not configured")
        return _fetch_github_repo(gh_client, ref)
    return RepoSummary(ref=ref, error=f"unknown provider {ref.provider}")


# ---------- Jenkins job fetching ----------

def _fetch_jenkins_job(jenkins_client, ref: JenkinsJobRef) -> JenkinsJobSummary:
    if jenkins_client is None:
        return JenkinsJobSummary(ref=ref, error="Jenkins not configured")
    ref_host = urlparse(ref.base_url).netloc
    client_host = urlparse(jenkins_client.base_url).netloc
    if ref_host != client_host:
        return JenkinsJobSummary(
            ref=ref,
            error=f"host mismatch (link {ref_host!r} vs client {client_host!r})",
        )
    url = f"{ref.base_url}{ref.job_path}/api/json"
    try:
        resp = jenkins_client.session.get(
            url, auth=jenkins_client.auth, timeout=jenkins_client.timeout
        )
    except requests.RequestException as exc:
        return JenkinsJobSummary(ref=ref, error=f"network error: {exc}")
    if resp.status_code == 404:
        return JenkinsJobSummary(ref=ref, exists=False, fetched=True)
    if resp.status_code >= 400:
        return JenkinsJobSummary(ref=ref, error=f"HTTP {resp.status_code}")
    try:
        data = resp.json() or {}
    except ValueError:
        return JenkinsJobSummary(ref=ref, error="non-JSON job response")

    disabled = bool(data.get("buildable") is False)
    last = data.get("lastCompletedBuild") or data.get("lastBuild") or {}
    last_number = last.get("number") if isinstance(last, dict) else None
    last_status = ""
    last_at = ""
    if last_number:
        # Second small call to grab result + timestamp — worth it for the verdict.
        try:
            build_resp = jenkins_client.session.get(
                f"{ref.base_url}{ref.job_path}/{last_number}/api/json",
                auth=jenkins_client.auth,
                timeout=jenkins_client.timeout,
            )
            if build_resp.status_code < 400:
                bd = build_resp.json() or {}
                last_status = str(bd.get("result") or "").upper()
                ts = bd.get("timestamp")
                if isinstance(ts, (int, float)):
                    from datetime import datetime, timezone
                    last_at = datetime.fromtimestamp(
                        ts / 1000, tz=timezone.utc
                    ).isoformat(timespec="seconds")
        except requests.RequestException:
            pass  # last-build detail is best-effort

    return JenkinsJobSummary(
        ref=ref,
        exists=True,
        disabled=disabled,
        last_build_number=last_number if isinstance(last_number, int) else None,
        last_build_status=last_status,
        last_build_at=last_at,
        fetched=True,
    )


# ---------- service candidate extraction ----------

# Kubernetes-ish resource names: lowercase alphanum + hyphens, 3-63 chars.
K8S_NAME_RE = re.compile(r"^[a-z]([-a-z0-9]{1,61}[a-z0-9])?$")


def _service_candidates(t: Ticket, max_candidates: int = 3) -> list[str]:
    """Return up to N candidate deployment names from JIRA components/labels.

    JIRA's Components field is the strongest signal — teams commonly name it
    after the service. Labels come next. Summary is deliberately skipped;
    parsing prose reliably is more trouble than it's worth.
    """
    candidates: list[str] = []
    for source in (t.components or []) + (t.labels or []):
        norm = (source or "").strip().lower().replace(" ", "-").replace("_", "-")
        if not K8S_NAME_RE.match(norm):
            continue
        if norm in candidates:
            continue
        candidates.append(norm)
        if len(candidates) >= max_candidates:
            break
    return candidates


# ---------- enrichment ----------

def enrich(
    jira: JiraClient,
    key: str,
    bb_client=None,
    gh_client=None,
    eks_client=None,
    jenkins_client=None,
) -> EnrichedTicket:
    ticket = jira.get_issue(key)
    remote = jira.remote_links(key)

    pr_refs = _collect_pr_refs(ticket, remote)
    prs = [_fetch_pr(bb_client, gh_client, ref) for ref in pr_refs]

    repo_refs = _collect_repo_refs(ticket, remote, pr_refs)
    repos = [_fetch_repo(bb_client, gh_client, ref) for ref in repo_refs]

    jenkins_refs = _collect_jenkins_refs(ticket, remote)
    jenkins_jobs = [_fetch_jenkins_job(jenkins_client, ref) for ref in jenkins_refs]

    # Everything else the LLM should still see, minus URLs we already probed.
    already_seen = (
        {p.ref.url for p in prs}
        | {r.ref.url for r in repos}
        | {j.ref.url for j in jenkins_jobs}
    )
    other = _collect_other_links(ticket, remote, already_seen)

    workload_probes: list[dict[str, Any]] = []
    if eks_client is not None:
        for candidate in _service_candidates(ticket):
            try:
                probe = eks_client.probe_workload(candidate)
            except Exception as exc:  # noqa: BLE001
                probe = {"found": False, "name": candidate, "error": str(exc)[:200]}
            workload_probes.append(probe)

    return EnrichedTicket(
        ticket=ticket,
        remote_links=remote,
        prs=prs,
        repos=repos,
        jenkins_jobs=jenkins_jobs,
        other_links=other,
        workload_probes=workload_probes,
    )


# ---------- prompt ----------

INVESTIGATION_SYSTEM = """You are helping an engineer decide whether a JIRA ticket describes a real, currently-active defect and, if so, how to resolve it.

This is a **read-only investigation**. Suggested resolutions may reference
commands the engineer could run, PRs to open, or people to contact — but
watcher itself does NOT execute writes; assume nothing has been changed on
their behalf.

Watcher probes the systems named in the ticket at investigation time and
gives you the results. Use the **live evidence** blocks as your primary
input. Treat "not fetched / not configured" entries as NO SIGNAL — never
as evidence.

Evidence weight (highest first):

1. **Live workload evidence** (kubectl):
   - Deployment Ready pods, low restart counts, no recent Warning events →
     lean LIKELY_INVALID unless comments contradict.
   - CrashLoopBackOff / ImagePullBackOff / non-zero restart counts /
     recent Warning events → lean REAL / LIKELY_REAL.
   - Deployment not found → NO SIGNAL.
2. **Live repo/PR evidence** (Bitbucket / GitHub API):
   - MERGED PR whose merge is AFTER the ticket's last activity → likely
     stale ticket, lean LIKELY_INVALID.
   - Repo **exists** (last activity recent) when the ticket asks to delete
     it → the cleanup is still pending; suggest a concrete deletion path.
   - Repo **not found (404)** when the ticket asks to delete it → the
     cleanup is DONE; lean INVALID / LIKELY_INVALID and suggest closing
     the ticket.
3. **Live Jenkins evidence**:
   - Job exists + last build SUCCESS + not disabled → lean LIKELY_INVALID
     if the ticket is about that job failing.
   - Job exists + last build FAILURE/ABORTED/UNSTABLE, or disabled →
     lean REAL, and point at the last build number in resolutions.
   - Job **not found (404)** when the ticket links a Jenkins URL → the
     URL is stale; explicitly say "Jenkins job is not present at that URL"
     and suggest alternative resolutions (search Jenkins, delete the link,
     etc.).
4. **Ticket comments** (medium weight) — especially anything from the
   last two weeks.
5. **Description** (lowest weight — often stale).

Output MUST follow this exact skeleton (plain markdown, no code fences):

**Verdict:** ONE_OF [REAL, LIKELY_REAL, NEEDS_INFO, LIKELY_INVALID, INVALID]

**Reasoning**
2 to 4 sentences citing the specific live-evidence signals that drove the
verdict. Quote the repo URL / PR number / job path / deployment name you
used. If you had no live evidence, say so explicitly and lean toward
NEEDS_INFO.

**Possible resolutions**
- up to 3 concrete next steps grounded in what watcher just verified.
  Reference PR numbers, code paths, config keys, deployment / pod names,
  Jenkins build numbers, or the person to talk to when they appear in the
  context. Do NOT tell the user to "check whether X exists" if watcher
  already checked — use the answer instead.

**Open questions** (omit section if none)
- up to 3 things that would change the verdict.

Be terse. No preamble, no sign-off."""


def _trim(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _format_pr_evidence(prs: list[PRSummary]) -> str:
    if not prs:
        return "(none)"
    lines = []
    for pr in prs:
        header = (
            f"- [{pr.ref.provider}] {pr.ref.owner_or_workspace}/"
            f"{pr.ref.repo}#{pr.ref.number}  {pr.ref.url}"
        )
        if pr.fetched:
            state_upper = (pr.state or "").upper()
            marker = ""
            if state_upper in {"MERGED"}:
                marker = " · **MERGED** — possible stale-ticket signal"
            elif state_upper in {"OPEN"}:
                marker = " · OPEN — fix not yet shipped"
            elif state_upper in {"CLOSED", "DECLINED"}:
                marker = f" · {state_upper}"
            header += marker
            header += f"\n    title: {_trim(pr.title, 200)}"
            if pr.author:
                header += f" · author={pr.author}"
            if pr.updated:
                header += f" · updated={pr.updated[:10]}"
        elif pr.error:
            header += f"\n    (metadata unavailable: {pr.error})"
        lines.append(header)
    return "\n".join(lines)


def _format_workload_evidence(probes: list[dict[str, Any]]) -> str:
    if not probes:
        return "(no service candidates in JIRA components/labels, or EKS not configured)"

    blocks: list[str] = []
    for p in probes:
        name = p.get("name") or "?"
        ns = p.get("namespace") or "?"
        if not p.get("found"):
            err = p.get("error")
            reason = f" ({err})" if err else ""
            blocks.append(f"- **{name}** (ns={ns}): not found{reason}")
            continue

        desired = p.get("replicas_desired")
        ready = p.get("replicas_ready", 0)
        avail = p.get("available_condition") or {}
        header = (
            f"- **{name}** (ns={ns}): ready {ready}/{desired}, "
            f"Available={avail.get('status')} "
            f"(reason={avail.get('reason') or '-'}, since "
            f"{(avail.get('last_transition') or '')[:10] or '-'})"
        )

        pod_lines: list[str] = []
        for pod in p.get("pods", []):
            state = "Ready" if pod.get("ready") else (pod.get("phase") or "?")
            waiting = ", ".join(pod.get("waiting") or [])
            waiting_str = f" waiting=[{waiting}]" if waiting else ""
            pod_lines.append(
                f"    · pod {pod.get('name')} → {state}, "
                f"restarts={pod.get('restarts', 0)}{waiting_str}"
            )
        event_lines: list[str] = []
        for ev in p.get("events", []):
            event_lines.append(
                f"    · event {ev.get('type')} {ev.get('reason')} "
                f"×{ev.get('count', 1)} @ {(ev.get('last') or '')[:16]} — "
                f"{_trim(ev.get('message') or '', 160)}"
            )
        blocks.append(
            "\n".join([header] + pod_lines + event_lines)
        )
    return "\n".join(blocks)


def _format_repo_evidence(repos: list[RepoSummary]) -> str:
    if not repos:
        return "(no repo URLs found in ticket, or no matching integration configured)"
    lines = []
    for r in repos:
        header = (
            f"- [{r.ref.provider}] {r.ref.owner_or_workspace}/"
            f"{r.ref.repo}  {r.ref.url}"
        )
        if r.fetched:
            if r.exists:
                header += " · **EXISTS**"
                bits = []
                if r.default_branch:
                    bits.append(f"default={r.default_branch}")
                if r.last_activity:
                    bits.append(f"last_activity={r.last_activity[:10]}")
                bits.append("private" if r.is_private else "public")
                if bits:
                    header += "  (" + ", ".join(bits) + ")"
            else:
                header += " · **NOT FOUND (404)** — repo already deleted or renamed"
        elif r.error:
            header += f"  (metadata unavailable: {r.error})"
        lines.append(header)
    return "\n".join(lines)


def _format_jenkins_evidence(jobs: list[JenkinsJobSummary]) -> str:
    if not jobs:
        return "(no Jenkins job URLs found in ticket, or Jenkins not configured)"
    lines = []
    for j in jobs:
        header = f"- {j.ref.base_url}{j.ref.job_path}  {j.ref.url}"
        if j.fetched:
            if j.exists:
                bits = ["**EXISTS**"]
                if j.disabled:
                    bits.append("**DISABLED**")
                if j.last_build_number is not None:
                    build_bit = f"last build #{j.last_build_number}"
                    if j.last_build_status:
                        build_bit += f" → {j.last_build_status}"
                    if j.last_build_at:
                        build_bit += f" ({j.last_build_at[:10]})"
                    bits.append(build_bit)
                header += " · " + " · ".join(bits)
            else:
                header += " · **NOT FOUND (404)** — job is not present at that URL"
        elif j.error:
            header += f"  (metadata unavailable: {j.error})"
        lines.append(header)
    return "\n".join(lines)


def build_prompt(et: EnrichedTicket, category: Optional[str]) -> str:
    t = et.ticket
    comments = "\n\n".join(
        f"— {c.author} @ {c.created[:10]}:\n{_trim(c.body, 500)}"
        for c in t.comments[-8:]
    ) or "(no comments)"

    prs_section = _format_pr_evidence(et.prs)
    repos_section = _format_repo_evidence(et.repos)
    jenkins_section = _format_jenkins_evidence(et.jenkins_jobs)
    workload_section = _format_workload_evidence(et.workload_probes)
    other_section = "\n".join(f"- {u}" for u in et.other_links) or "(none)"

    components = ", ".join(t.components) or "(none)"
    labels = ", ".join(t.labels) or "(none)"

    return f"""{INVESTIGATION_SYSTEM}

---
Ticket: {t.key} — {_trim(t.summary, 200)}
URL: {t.url}
Status: {t.status} · Priority: {t.priority} · Type: {t.issue_type}
Reporter: {t.reporter}
Components: {components} · Labels: {labels}
Category (watcher memory): {category or "unknown"}
Updated: {t.updated[:10]}

**Live workload evidence** (via kubectl, pulled just now)
{workload_section}

**Live repo evidence** (via Bitbucket / GitHub API, pulled just now)
{repos_section}

**PR evidence**
{prs_section}

**Live Jenkins evidence** (via Jenkins API, pulled just now)
{jenkins_section}

**Other linked resources**
{other_section}

**Comments (chronological, latest ~8)**
{comments}

**Description** (may be stale — treat as background, not authoritative)
{_trim(t.description, 2500) or "(empty)"}
"""


# ---------- entry point ----------

@dataclass
class InvestigationResult:
    key: str
    url: str
    summary: str
    verdict_line: str
    body: str
    error: Optional[str] = None
    warnings: list[str] = field(default_factory=list)


VERDICT_RE = re.compile(r"^\**Verdict:?\**\s*[:\-]?\s*(REAL|LIKELY_REAL|NEEDS_INFO|LIKELY_INVALID|INVALID)", re.MULTILINE | re.IGNORECASE)


def _extract_verdict(text: str) -> str:
    m = VERDICT_RE.search(text or "")
    return (m.group(1).upper() if m else "").strip()


def _collect_warnings(et: EnrichedTicket) -> list[str]:
    """Turn extracted-but-unprobed URLs into a visible warning list.

    Most common case: the ticket mentions a Bitbucket repo but
    BITBUCKET_API_TOKEN isn't set — the probe silently returns "not
    configured", the LLM ends up hedging, and the user is left wondering
    why watcher didn't check.
    """
    out: list[str] = []
    for r in et.repos:
        if not r.fetched and r.error and "not configured" in r.error.lower():
            provider = r.ref.provider.replace("-", " ").title().replace("Cloud", "Cloud").replace("Dc", "DC")
            out.append(
                f"{provider} URL detected ({r.ref.url}) but {r.error}. "
                f"Configure it in .env and re-run to get a live check."
            )
    for pr in et.prs:
        if not pr.fetched and pr.error and "not configured" in pr.error.lower():
            out.append(
                f"{pr.ref.provider} PR URL detected "
                f"({pr.ref.owner_or_workspace}/{pr.ref.repo}#{pr.ref.number}) "
                f"but {pr.error}. Configure it in .env to probe PR state."
            )
    for j in et.jenkins_jobs:
        if not j.fetched and j.error and (
            "not configured" in j.error.lower()
            or "host mismatch" in j.error.lower()
        ):
            out.append(f"Jenkins URL detected but {j.error}.")
    return out


def investigate(
    keys: list[str],
    jira: JiraClient,
    memory: Memory,
    provider: str,
    cli_override: Optional[str] = None,
    bb_client=None,
    gh_client=None,
    eks_client=None,
    jenkins_client=None,
) -> list[InvestigationResult]:
    results: list[InvestigationResult] = []
    for key in keys:
        try:
            et = enrich(
                jira,
                key,
                bb_client=bb_client,
                gh_client=gh_client,
                eks_client=eks_client,
                jenkins_client=jenkins_client,
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                InvestigationResult(
                    key=key, url="", summary="", verdict_line="",
                    body="", error=f"fetch failed: {exc}",
                )
            )
            continue

        record = memory.get(key)
        category = record.category if record else None
        prompt = build_prompt(et, category)

        try:
            raw = run_provider(provider, prompt, cli_override=cli_override)
        except ProviderError as exc:
            results.append(
                InvestigationResult(
                    key=key, url=et.ticket.url, summary=et.ticket.summary,
                    verdict_line="", body="", error=f"LLM failed: {exc}",
                )
            )
            continue

        verdict = _extract_verdict(raw)
        results.append(
            InvestigationResult(
                key=key,
                url=et.ticket.url,
                summary=et.ticket.summary,
                verdict_line=verdict,
                body=raw,
                warnings=_collect_warnings(et),
            )
        )
    return results


def resolve_keys_for_category(
    memory: Memory, tickets: list[Ticket], category: str, limit: int
) -> list[str]:
    """Return currently-assigned tickets whose cached category matches."""
    keys = []
    for t in tickets:
        rec = memory.get(t.key)
        if rec and rec.category.lower() == category.lower():
            keys.append(t.key)
        if len(keys) >= limit:
            break
    return keys
