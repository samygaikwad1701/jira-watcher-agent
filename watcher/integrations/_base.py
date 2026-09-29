"""Shared helpers for integration clients."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class IntegrationStatus:
    """Outcome of a single connectivity verification."""

    name: str
    ok: bool
    identity: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    hint: Optional[str] = None

    @classmethod
    def skipped(cls, name: str, reason: str) -> "IntegrationStatus":
        return cls(name=name, ok=False, error=f"not configured: {reason}", hint="set the required env vars in .env")


def keychain_password(service: str, account: str) -> Optional[str]:
    """Return a macOS keychain secret, or None. No-ops off macOS."""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", service, "-w"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return None
    token = (result.stdout or "").strip()
    return token or None
