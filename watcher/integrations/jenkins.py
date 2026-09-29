"""Jenkins connectivity.

Ported from `engineering-knowledge-base/skills/jenkins/scripts/jenkins_common.py`.
Watcher supports a single Jenkins instance (unprefixed env vars). The KB's
multi-instance pattern (JENKINS_LEGACY_*, JENKINS_AWS_*) can be layered on
later if needed.
"""

from __future__ import annotations

import os
from typing import Optional

import requests
from requests.auth import HTTPBasicAuth

from ._base import IntegrationStatus, keychain_password


USER_AGENT = "watcher-jenkins/0.1"


class JenkinsClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        token: str,
        timeout: int = 20,
    ):
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.token = token
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"Accept": "application/json", "User-Agent": USER_AGENT}
        )
        self.auth = HTTPBasicAuth(username, token)

    def verify(self) -> IntegrationStatus:
        url = f"{self.base_url}/whoAmI/api/json"
        try:
            resp = self.session.get(url, auth=self.auth, timeout=self.timeout)
        except requests.RequestException as exc:
            return IntegrationStatus(
                name="jenkins", ok=False, error=f"network error: {exc}"
            )

        if resp.status_code >= 400:
            return IntegrationStatus(
                name="jenkins",
                ok=False,
                error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                hint=(
                    "Get an API token from your Jenkins user profile → "
                    "Configure → API Token. Use your Jenkins username, not "
                    "your email."
                ),
            )

        data = resp.json() if resp.content else {}
        return IntegrationStatus(
            name="jenkins",
            ok=bool(data.get("authenticated", True)),
            identity=str(data.get("fullName") or data.get("name") or self.username),
            details={
                "base_url": self.base_url,
                "authenticated": data.get("authenticated", True),
            },
        )

    def crumb(self) -> Optional[dict[str, str]]:
        """Fetch a CSRF crumb for POST requests. 404-tolerant."""
        try:
            resp = self.session.get(
                f"{self.base_url}/crumbIssuer/api/json",
                auth=self.auth,
                timeout=self.timeout,
            )
        except requests.RequestException:
            return None
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            return None
        data = resp.json()
        field = data.get("crumbRequestField")
        crumb = data.get("crumb")
        if not (field and crumb):
            return None
        return {field: crumb}


def from_env() -> Optional[JenkinsClient]:
    base = (os.environ.get("JENKINS_BASE_URL") or "").strip()
    user = (os.environ.get("JENKINS_USERNAME") or "").strip()
    token = (os.environ.get("JENKINS_API_TOKEN") or "").strip()
    if base and user and not token:
        token = keychain_password("jenkins-api-token", user) or ""
    if not (base and user and token):
        return None
    return JenkinsClient(base, user, token)
