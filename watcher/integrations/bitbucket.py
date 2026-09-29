"""Bitbucket connectivity (Cloud + Data Center).

Cloud: Basic auth with `EMAIL:API_TOKEN` (Atlassian API token — NOT the
`x-token-auth` git-clone credential). Data Center: Bearer PAT.

Ported from `engineering-knowledge-base/skills/bitbucket-agent/scripts/
bitbucket_common.py`.
"""

from __future__ import annotations

import os
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth

from ._base import IntegrationStatus, keychain_password

DEFAULT_HOST = "bitbucket.org"
USER_AGENT = "watcher-bitbucket/0.1"


class BitbucketClient:
    def __init__(
        self,
        host: str,
        workspace: str,
        email: str,
        token: str,
        auth_mode: str = "basic",
        api_base: Optional[str] = None,
        timeout: int = 20,
    ):
        self.host = host
        self.workspace = workspace
        self.email = email
        self.token = token
        self.auth_mode = auth_mode
        self.timeout = timeout
        self.is_cloud = host == "bitbucket.org" or "api.bitbucket.org" in (api_base or "")
        self.api_base = (
            api_base
            or (
                "https://api.bitbucket.org/2.0" if self.is_cloud else f"https://{host}/rest/api/1.0"
            )
        ).rstrip("/")

        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": USER_AGENT})
        if auth_mode == "bearer":
            self.session.headers["Authorization"] = f"Bearer {token}"
            self.auth = None
        else:  # basic
            if not email:
                raise RuntimeError("BITBUCKET_EMAIL required for basic auth")
            self.auth = HTTPBasicAuth(email, token)

    def verify(self) -> IntegrationStatus:
        try:
            if self.is_cloud:
                url = f"{self.api_base}/user"
            else:
                # Data Center: look up the calling user via their username.
                url = f"{self.api_base}/users/{self.email.split('@')[0]}"
            resp = self.session.get(url, auth=self.auth, timeout=self.timeout)
        except requests.RequestException as exc:
            return IntegrationStatus(name="bitbucket", ok=False, error=f"network error: {exc}")

        if resp.status_code >= 400:
            return IntegrationStatus(
                name="bitbucket",
                ok=False,
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                hint=(
                    "For Cloud, use an Atlassian API token from "
                    "id.atlassian.com/manage-profile/security/api-tokens. "
                    "Bitbucket's own 'app password' won't work here."
                ),
            )

        data = resp.json() if resp.content else {}
        identity = (
            data.get("username")
            or data.get("nickname")
            or data.get("name")
            or data.get("display_name")
            or self.email
        )
        return IntegrationStatus(
            name="bitbucket",
            ok=True,
            identity=str(identity),
            details={
                "workspace": self.workspace,
                "cloud": self.is_cloud,
                "api_base": self.api_base,
            },
        )


def from_env() -> Optional[BitbucketClient]:
    token = (os.environ.get("BITBUCKET_API_TOKEN") or "").strip()
    email = (
        os.environ.get("BITBUCKET_EMAIL") or os.environ.get("BITBUCKET_USERNAME") or ""
    ).strip()
    if not token and email:
        token = keychain_password("bitbucket-api-token", email) or ""
    if not token:
        return None
    if not email:
        return None

    return BitbucketClient(
        host=os.environ.get("BITBUCKET_HOST", DEFAULT_HOST).strip(),
        workspace=(os.environ.get("BITBUCKET_WORKSPACE") or "").strip(),
        email=email,
        token=token,
        auth_mode=(os.environ.get("BITBUCKET_AUTH_MODE", "basic") or "basic").strip().lower(),
        api_base=(os.environ.get("BITBUCKET_API_BASE") or None),
    )
