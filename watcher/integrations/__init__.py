"""Connectivity integrations for downstream systems.

Each module exposes:

- A `Client` class with a `verify()` method returning `IntegrationStatus`.
- A `from_env()` factory that returns a configured client or `None` when the
  required env vars are missing (i.e. the integration is not configured).

The registry `INTEGRATIONS` maps short names to `from_env` factories so the
CLI can iterate uniformly.
"""

from ._base import IntegrationStatus
from . import bitbucket, eks, github, jenkins


INTEGRATIONS = {
    "bitbucket": bitbucket.from_env,
    "jenkins": jenkins.from_env,
    "github": github.from_env,
    "eks": eks.from_env,
}


__all__ = ["INTEGRATIONS", "IntegrationStatus", "bitbucket", "eks", "github", "jenkins"]
