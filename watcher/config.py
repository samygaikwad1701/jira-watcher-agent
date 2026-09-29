import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


DEFAULT_JQL = "assignee = currentUser() AND statusCategory != Done ORDER BY updated DESC"
SUPPORTED_PROVIDERS = ("claude", "codex", "cursor")
SUPPORTED_FLAVORS = ("cloud", "server")


def _default_memory_path() -> str:
    """Prefer `<project-root>/memory/memory.json` when watcher is installed
    from source (editable install — we detect this by looking for a
    `pyproject.toml` next to the package). Fall back to
    `~/.watcher/memory.json` for real PyPI installs, where writing into
    site-packages would be clobbered on upgrade and rejected under
    read-only or shared installs.
    """
    pkg_dir = Path(__file__).resolve().parent  # .../<install>/watcher/
    for candidate in (pkg_dir.parent, pkg_dir):
        if (candidate / "pyproject.toml").is_file():
            return str(candidate / "memory" / "memory.json")
    return str(Path.home() / ".watcher" / "memory.json")


DEFAULT_MEMORY_PATH = _default_memory_path()


@dataclass(frozen=True)
class Config:
    jira_base_url: str
    jira_flavor: str
    jira_email: Optional[str]
    jira_api_token: str
    jira_cloud_id: Optional[str]
    jira_user_agent: Optional[str]
    jira_jql: str
    llm_provider: str
    llm_cli_path: Optional[str]
    watch_interval_seconds: int
    memory_path: str

    def with_overrides(self, **kwargs) -> "Config":
        return replace(self, **kwargs)


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _auto_flavor(base_url: str, token: str = "") -> str:
    """Guess cloud vs. server when JIRA_FLAVOR is not set.

    URL alone isn't enough — Atlassian Cloud supports custom vanity domains
    (e.g. jira.mycompany.com) that don't match `*.atlassian.net`. Modern
    Atlassian API tokens start with `ATATT`; if we see that prefix we treat
    the deployment as Cloud regardless of URL.
    """
    host = base_url.lower()
    if ".atlassian.net" in host or ".jira.com" in host:
        return "cloud"
    if token.startswith("ATATT") or token.startswith("ATAT"):
        return "cloud"
    return "server"


def load_config() -> Config:
    load_dotenv()

    provider = os.environ.get("LLM_PROVIDER", "claude").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise RuntimeError(
            f"LLM_PROVIDER must be one of {SUPPORTED_PROVIDERS}, got {provider!r}."
        )

    base_url = _require("JIRA_BASE_URL").rstrip("/")
    token = _require("JIRA_API_TOKEN")
    flavor = (
        os.environ.get("JIRA_FLAVOR", "").strip().lower()
        or _auto_flavor(base_url, token)
    )
    if flavor not in SUPPORTED_FLAVORS:
        raise RuntimeError(
            f"JIRA_FLAVOR must be one of {SUPPORTED_FLAVORS}, got {flavor!r}."
        )

    email = (os.environ.get("JIRA_EMAIL") or "").strip() or None
    if flavor == "cloud" and not email:
        raise RuntimeError(
            "JIRA_EMAIL is required when JIRA_FLAVOR=cloud (Basic auth). "
            "For self-hosted JIRA use JIRA_FLAVOR=server and omit the email."
        )

    return Config(
        jira_base_url=base_url,
        jira_flavor=flavor,
        jira_email=email,
        jira_api_token=token,
        jira_cloud_id=(os.environ.get("JIRA_CLOUD_ID") or "").strip() or None,
        jira_user_agent=(os.environ.get("JIRA_USER_AGENT") or "").strip() or None,
        jira_jql=os.environ.get("JIRA_JQL", DEFAULT_JQL).strip() or DEFAULT_JQL,
        llm_provider=provider,
        llm_cli_path=os.environ.get("LLM_CLI_PATH") or None,
        watch_interval_seconds=int(os.environ.get("WATCHER_INTERVAL", "300")),
        memory_path=os.environ.get("WATCHER_MEMORY_PATH", DEFAULT_MEMORY_PATH),
    )
