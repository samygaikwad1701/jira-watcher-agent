"""LLM providers — each wraps a locally-installed coding CLI.

Uses the CLI's non-interactive mode so we can capture stdout. This avoids
juggling API keys per provider — the user's existing CLI subscription handles
auth. The prompt is passed via stdin to sidestep ARG_MAX on large payloads.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

DEFAULT_TIMEOUT_SEC = 180


@dataclass
class ProviderSpec:
    name: str
    binary: str
    args: list[str]
    install_hint: str


PROVIDERS: dict[str, ProviderSpec] = {
    "claude": ProviderSpec(
        name="claude",
        binary="claude",
        args=["-p", "--output-format", "text"],
        install_hint="Install Claude Code: https://claude.com/claude-code",
    ),
    "codex": ProviderSpec(
        name="codex",
        binary="codex",
        args=["exec", "-"],
        install_hint="Install OpenAI Codex CLI: https://github.com/openai/codex",
    ),
    "cursor": ProviderSpec(
        name="cursor",
        binary="cursor-agent",
        args=["-p"],
        install_hint="Install Cursor Agent CLI: https://cursor.com/cli",
    ),
}


class ProviderError(RuntimeError):
    pass


def _resolve_binary(spec: ProviderSpec, override: str | None) -> str:
    binary = override or spec.binary
    path = shutil.which(binary)
    if not path:
        raise ProviderError(
            f"{spec.name!r} CLI not found on PATH (looked for {binary!r}). {spec.install_hint}"
        )
    return path


def run_provider(
    provider_name: str,
    prompt: str,
    cli_override: str | None = None,
    timeout: int = DEFAULT_TIMEOUT_SEC,
) -> str:
    """Send `prompt` via stdin to the selected provider CLI and return stdout."""
    spec = PROVIDERS.get(provider_name)
    if spec is None:
        raise ProviderError(
            f"Unknown provider {provider_name!r}. Supported: {', '.join(PROVIDERS)}."
        )

    binary = _resolve_binary(spec, cli_override)
    cmd = [binary, *spec.args]

    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ProviderError(f"{spec.name} timed out after {timeout}s") from exc

    if result.returncode != 0:
        stderr = (result.stderr or "").strip()[:500]
        raise ProviderError(f"{spec.name} exited {result.returncode}: {stderr or '<no stderr>'}")

    return (result.stdout or "").strip()
