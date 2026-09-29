from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

import requests
from requests.auth import HTTPBasicAuth

TICKET_FIELDS = [
    "summary",
    "status",
    "priority",
    "issuetype",
    "updated",
    "created",
    "duedate",
    "reporter",
    "labels",
    "components",
    "description",
    "comment",
]

# Cloudflare/AtlassianEdge blocks the default python-requests UA on REST paths.
DEFAULT_UA = "Mozilla/5.0 (compatible; watcher/0.1; +https://github.com/) python-requests"

CLOUD_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")


@dataclass
class Comment:
    author: str
    created: str
    body: str


@dataclass
class Ticket:
    key: str
    url: str
    summary: str
    status: str
    status_category: str
    priority: str
    issue_type: str
    updated: str
    created: str
    due_date: Optional[str]
    reporter: str
    labels: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)
    description: str = ""
    comments: list[Comment] = field(default_factory=list)

    @property
    def signature(self) -> tuple[str, str]:
        return (self.key, self.updated)


def _adf_to_text(node: Any) -> str:
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(_adf_to_text(child) for child in node)
    if not isinstance(node, dict):
        return ""

    node_type = node.get("type")
    if node_type == "text":
        return node.get("text", "")

    text = _adf_to_text(node.get("content"))
    if node_type in {"paragraph", "heading", "listItem", "blockquote", "codeBlock"}:
        return text + "\n"
    if node_type in {"bulletList", "orderedList"}:
        return text + "\n"
    if node_type == "hardBreak":
        return "\n"
    return text


def _text_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return _adf_to_text(value).strip()


def _parse_comment(raw: dict[str, Any]) -> Comment:
    author = (raw.get("author") or {}).get("displayName") or "Unknown"
    return Comment(
        author=author,
        created=raw.get("created", ""),
        body=_text_field(raw.get("body")),
    )


def _parse_ticket(browse_base: str, raw: dict[str, Any]) -> Ticket:
    key = raw["key"]
    fields = raw.get("fields") or {}
    status = fields.get("status") or {}
    status_cat = (status.get("statusCategory") or {}).get("name") or ""
    priority = (fields.get("priority") or {}).get("name") or "None"
    issue_type = (fields.get("issuetype") or {}).get("name") or ""
    reporter = (fields.get("reporter") or {}).get("displayName") or "Unknown"
    comments_raw = ((fields.get("comment") or {}).get("comments")) or []
    comments = [_parse_comment(c) for c in comments_raw]

    return Ticket(
        key=key,
        url=f"{browse_base}/browse/{key}",
        summary=fields.get("summary") or "",
        status=status.get("name") or "",
        status_category=status_cat,
        priority=priority,
        issue_type=issue_type,
        updated=fields.get("updated") or "",
        created=fields.get("created") or "",
        due_date=fields.get("duedate"),
        reporter=reporter,
        labels=list(fields.get("labels") or []),
        components=[c.get("name", "") for c in (fields.get("components") or []) if c.get("name")],
        description=_text_field(fields.get("description")),
        comments=comments,
    )


class JiraClient:
    """Handles three deployment shapes:

    - **cloud**: JIRA Cloud accessed via Atlassian's platform gateway
      (`https://api.atlassian.com/ex/jira/<cloudId>`). Scoped API tokens
      (`ATATT…`) only authenticate against the gateway, not the site URL —
      even when the site is `<name>.atlassian.net`. The `cloudId` is
      auto-discovered from `<site>/_edge/tenant_info`.
    - **server**: self-hosted JIRA Server / Data Center with a Personal
      Access Token (Bearer auth), REST v2, plain-text description fields.

    In both cases the `/browse/<KEY>` link URL still uses the site URL,
    not the API base.
    """

    def __init__(
        self,
        base_url: str,
        flavor: str,
        api_token: str,
        email: Optional[str] = None,
        cloud_id: Optional[str] = None,
        user_agent: Optional[str] = None,
        timeout: int = 30,
        debug: bool = False,
    ):
        self.site_url = base_url.rstrip("/")
        self.flavor = flavor
        self.api_token = api_token
        self.email = email
        self.timeout = timeout
        self.debug = debug
        self.cloud_id: Optional[str] = cloud_id

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": user_agent or DEFAULT_UA,
            }
        )

        if flavor == "cloud":
            if not email:
                raise RuntimeError("JIRA_EMAIL is required for cloud flavor.")
            self.auth = HTTPBasicAuth(email, api_token)
            self.api_version = "3"
            self.api_base_url = self._resolve_cloud_api_base()
        elif flavor == "server":
            self.auth = None
            self.session.headers["Authorization"] = f"Bearer {api_token}"
            self.api_version = "2"
            self.api_base_url = self.site_url
        else:
            raise RuntimeError(f"Unknown JIRA flavor {flavor!r}. Use 'cloud' or 'server'.")

    # ---------- diagnostics ----------

    def _log(self, msg: str) -> None:
        if self.debug:
            print(f"[watcher:jira] {msg}", flush=True)

    # ---------- cloud id discovery ----------

    def _resolve_cloud_api_base(self) -> str:
        """Return the gateway API base for cloud, or fall back to site URL."""
        cloud_id = self.cloud_id or self._discover_cloud_id()
        if cloud_id and CLOUD_ID_RE.match(cloud_id):
            self.cloud_id = cloud_id
            base = f"https://api.atlassian.com/ex/jira/{cloud_id}"
            self._log(f"cloud gateway resolved: {base}")
            return base
        self._log("cloud gateway unresolved; falling back to site URL")
        return self.site_url

    def _discover_cloud_id(self) -> str:
        url = f"{self.site_url}/_edge/tenant_info"
        try:
            resp = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            self._log(f"tenant_info request failed: {exc}")
            return ""
        if resp.status_code >= 400:
            self._log(f"tenant_info returned {resp.status_code}")
            return ""
        try:
            data = resp.json()
        except ValueError:
            return ""
        return str(data.get("cloudId") or "").strip()

    # ---------- URL helpers ----------

    def _url(self, path: str) -> str:
        return f"{self.api_base_url}/rest/api/{self.api_version}/{path.lstrip('/')}"

    # ---------- endpoints ----------

    def whoami(self) -> dict[str, Any]:
        url = self._url("myself")
        resp = self.session.get(url, auth=self.auth, timeout=self.timeout)
        if resp.status_code >= 400:
            hint = self._auth_hint(resp)
            raise RuntimeError(
                f"JIRA /myself failed ({resp.status_code}) at {url}\n"
                f"  Response: {resp.text[:300]}\n{hint}"
            )
        return resp.json()

    def get_issue(self, key: str) -> Ticket:
        """Fetch a single issue with all fields for deep investigation."""
        url = self._url(f"issue/{key}")
        params = {"fields": "*all", "expand": "renderedFields"}
        resp = self.session.get(url, params=params, auth=self.auth, timeout=self.timeout)
        self._check(resp, f"get_issue({key})")
        return _parse_ticket(self.site_url, resp.json())

    def remote_links(self, key: str) -> list[dict[str, Any]]:
        """Fetch external links (PRs, wiki pages, etc.) attached to the issue."""
        url = self._url(f"issue/{key}/remotelink")
        resp = self.session.get(url, auth=self.auth, timeout=self.timeout)
        if resp.status_code == 404:
            return []
        self._check(resp, f"remotelink({key})")
        data = resp.json() or []
        return data if isinstance(data, list) else []

    def _auth_hint(self, resp: requests.Response) -> str:
        token = self.api_token or ""
        www_auth = resp.headers.get("WWW-Authenticate", "<none>")
        server_hdr = resp.headers.get("Server", "<none>")

        lines = [
            "  Diagnostics:",
            f"    site_url     : {self.site_url}",
            f"    api_base_url : {self.api_base_url}",
            f"    cloud_id     : {self.cloud_id or '<none>'}",
            f"    flavor       : {self.flavor}",
            f"    email        : {self.email!r}",
            f"    token        : len={len(token)}, "
            f"starts={token[:4]!r}, ends={token[-4:]!r} (never logged in full)",
            f"    WWW-Authenticate : {www_auth}",
            f"    Server           : {server_hdr}",
            "",
        ]
        if self.flavor == "cloud":
            lines += [
                "  Cloud auth causes:",
                "    1. Scoped API tokens (ATATT…) only authenticate against the",
                "       Atlassian platform gateway (api.atlassian.com/ex/jira/<id>).",
                "       Watcher tries to discover the cloudId from",
                f"         {self.site_url}/_edge/tenant_info",
                "       If that discovery failed, set JIRA_CLOUD_ID explicitly in .env.",
                "    2. JIRA_EMAIL is not the login email of the token's owner.",
                "    3. Token has scopes that don't include Jira read.",
                "    4. Token pasted with whitespace or quotes.",
            ]
        else:
            lines += [
                "  Server / DC auth causes:",
                "    1. Token is not a Personal Access Token — create one at",
                f"       {self.site_url}/secure/ViewProfile.jspa → Personal Access Tokens.",
                "    2. Token revoked / expired / lacks permissions.",
                "    3. Token pasted with whitespace or quotes — check .env.",
            ]
        return "\n".join(lines)

    # ---------- search ----------

    def search(self, jql: str, page_size: int = 50) -> list[Ticket]:
        if self.flavor == "cloud":
            return self._search_cloud(jql, page_size)
        return self._search_server(jql, page_size)

    def _search_cloud(self, jql: str, page_size: int) -> list[Ticket]:
        primary = self._url("search/jql")
        fallback = self._url("search")
        tickets: list[Ticket] = []
        next_token: Optional[str] = None
        start_at = 0
        page = 0
        use_fallback = False

        while True:
            page += 1
            if not use_fallback:
                payload: dict[str, Any] = {
                    "jql": jql,
                    "fields": TICKET_FIELDS,
                    "maxResults": page_size,
                }
                if next_token:
                    payload["nextPageToken"] = next_token
                url = primary
            else:
                payload = {
                    "jql": jql,
                    "fields": TICKET_FIELDS,
                    "maxResults": page_size,
                    "startAt": start_at,
                }
                url = fallback

            self._log(f"[cloud] POST {url} page={page}")
            resp = self.session.post(url, json=payload, auth=self.auth, timeout=self.timeout)
            if not use_fallback and resp.status_code in (404, 410):
                self._log(f"  search/jql {resp.status_code} → falling back to /search")
                use_fallback = True
                continue
            self._check(resp, "search")
            data = resp.json()
            issues = data.get("issues") or []
            total = data.get("total")
            self._log(
                f"  → 200 issues={len(issues)} total={total} "
                f"hasToken={bool(data.get('nextPageToken'))}"
            )
            tickets.extend(_parse_ticket(self.site_url, i) for i in issues)

            if not use_fallback:
                next_token = data.get("nextPageToken")
                if not next_token:
                    break
            else:
                start_at += len(issues)
                if not issues or (total is not None and start_at >= total):
                    break
        return tickets

    def _search_server(self, jql: str, page_size: int) -> list[Ticket]:
        url = self._url("search")
        tickets: list[Ticket] = []
        start_at = 0
        page = 0
        while True:
            page += 1
            payload = {
                "jql": jql,
                "startAt": start_at,
                "maxResults": page_size,
                "fields": TICKET_FIELDS,
            }
            self._log(f"[server] POST {url} page={page} startAt={start_at}")
            resp = self.session.post(url, json=payload, timeout=self.timeout)
            self._check(resp, "search")
            data = resp.json()
            issues = data.get("issues") or []
            total = data.get("total", 0)
            self._log(f"  → 200 issues={len(issues)} total={total}")
            tickets.extend(_parse_ticket(self.site_url, i) for i in issues)
            start_at += len(issues)
            if not issues or start_at >= total:
                break
        return tickets

    def _check(self, resp: requests.Response, op: str) -> None:
        if resp.status_code < 400:
            return
        hint = self._auth_hint(resp) if resp.status_code in (401, 403) else ""
        raise RuntimeError(
            f"JIRA {op} failed ({resp.status_code}): {resp.text[:500]}"
            + (f"\n{hint}" if hint else "")
        )

    def assigned_to_me(self, jql: str) -> list[Ticket]:
        return self.search(jql)
