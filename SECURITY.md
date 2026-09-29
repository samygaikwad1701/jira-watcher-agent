# Security Policy

## Supported versions

This project is in public alpha (`0.x`). Only the latest published minor
version receives security fixes; older versions may not be patched.

| Version | Supported |
|---------|-----------|
| 0.1.x   | ✅        |
| < 0.1   | ❌        |

## Reporting a vulnerability

**Do not open a public issue.** If you believe you've found a security
issue in `jira-watcher-agent`, please report it privately:

1. Preferred: use GitHub's private vulnerability reporting on this repo:
   `Security` tab → `Report a vulnerability`.
2. Alternative: email the maintainer at the address in
   [`pyproject.toml`'s `authors`](pyproject.toml) with subject line
   `[SECURITY] jira-watcher-agent — <one-line summary>`.

Please include:

- A description of the issue and its impact.
- The exact version affected (`watcher --version`).
- Steps to reproduce, or proof-of-concept code if you have one.
- Any suggested mitigation.

You should get an acknowledgement within **72 hours**. A fix and public
advisory typically follow within **14 days** for high-severity issues.

## Threat model — what's in scope

- Secret leakage (JIRA / Bitbucket / GitHub / AWS credentials) into logs,
  memory files, prompts, or telemetry.
- Injection into shell commands or LLM prompts via ticket content.
- Read-only guarantee bypass on the `kubectl` probe path (any code that
  lets an untrusted input drive a write subcommand is in scope).
- Path-traversal in the memory file resolver.
- Auth bypass against the JIRA / gateway URL flow.

## Out of scope

- Vulnerabilities in transitive dependencies (`requests`, `rich`,
  `python-dotenv`) that are already tracked upstream — please report those
  to the respective projects.
- Social-engineering scenarios that require the user to run a malicious
  `claude` / `codex` / `cursor-agent` binary.
- Denial of service against your own JIRA / Bitbucket by choosing an
  unbounded JQL — that's user error, not a defect.

## Handling credentials

Watcher never phones home. All API traffic is direct between your local
process and the systems configured in `.env`. The memory file
(`memory/memory.json` or `~/.watcher/memory.json`) stores ticket keys,
summaries, and category tags — no tokens, no descriptions, no comments.
