# Changelog

All notable changes to `jira-watcher-agent` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

Version numbers are set in [`watcher/__init__.py`](watcher/__init__.py) and
propagate to `pyproject.toml` at build time; the git tag matches the same
string prefixed with `v` (e.g. `v0.1.0`).

## [Unreleased]

## [0.1.0] — 2026-09-28

Initial public release.

### Added

- **JIRA digest** — LLM-authored triage of every ticket assigned to you,
  grouped by category, with a category count table.
- **Watch mode** (`--watch`) — polls JIRA; only re-runs the LLM when a
  ticket signature changes.
- **Persistent memory** — per-ticket `(key, updated)` fingerprint + cached
  category, stored at `<project-root>/memory/memory.json` when installed
  from source, or `~/.watcher/memory.json` on a PyPI install.
- **Provider abstraction** — supports `claude`, `codex`, and
  `cursor-agent` CLIs; no separate API keys required.
- **JIRA Cloud + Server/DC support** — including custom vanity domains
  with automatic `cloudId` discovery.
- **Integration connectivity checks** (`--check [target]`) for Bitbucket,
  Jenkins, GitHub, and EKS with per-provider `whoami` probes.
- **Resolve stage** (`--resolve`) — deep-investigate a ticket:
  - Full JIRA payload (description, all comments, remote links).
  - Live PR evidence (Bitbucket Cloud + DC + GitHub).
  - Live repo existence probes (EXISTS / 404).
  - Live Jenkins job probes (existence + last build result).
  - Live EKS workload probes via read-only `kubectl` (deployment,
    pods, events). Interactive `kubectl` context picker before probes.
- **Read-only investigation guarantee** — `kubectl` calls are guarded
  against write subcommands at the code level.
- **Interactive post-digest prompt** — auto-detects category vs. JIRA
  keys from a single input.
- **Clickable verdict** — OSC 8 hyperlinks in supporting terminals.
- **`--version`** flag prints the running package version.

[Unreleased]: https://github.com/samygaikwad1701/jira-watcher-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/samygaikwad1701/jira-watcher-agent/releases/tag/v0.1.0
