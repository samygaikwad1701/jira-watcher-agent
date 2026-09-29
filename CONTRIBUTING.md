# Contributing to jira-watcher-agent

Thanks for your interest. This is a small, opinionated tool — contributions
that keep the surface area lean are especially welcome.

## Ground rules

- **Every change lands via a pull request.** `main` is protected; direct
  pushes are blocked.
- **Discuss non-trivial changes first.** For anything larger than a one-file
  fix, open an issue describing the problem and proposed direction before
  you write code.
- **Keep PRs small and focused.** One concern per PR — bug fix, feature,
  refactor, or docs. Split noisy formatting from behaviour changes.
- **Match the existing style.** Watcher uses `ruff` for lint + format; CI
  fails on style violations.

## Development setup

```bash
git clone git@github.com:samygaikwad1701/jira-watcher-agent.git
cd jira-watcher-agent
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'   # or just pip install -e . && pip install ruff mypy
cp .env.example .env      # fill in your JIRA credentials
```

Sanity check:

```bash
python -m compileall -q watcher/
watcher --version
ruff check watcher/
ruff format --check watcher/
```

## Making a change

1. Fork the repo (or create a topic branch if you have write access).
2. Branch from `main`: `git checkout -b fix/short-slug` or
   `feat/short-slug`.
3. Make the change. Include tests where practical; smoke-test the CLI end
   to end when the change touches user-visible behaviour.
4. Run the checks above before pushing.
5. Push and open a PR against `main`.
6. Fill in the PR template — especially the "how was this tested?" section.
   Screenshots or terminal captures are welcome for anything that changes
   the CLI output.

## Commit messages

- Short imperative subject (`fix jenkins host mismatch when port differs`),
  ≤ 72 chars.
- Blank line, then a paragraph explaining *why* — not what. The diff shows
  what.
- Reference issues with `Fixes #123` / `Refs #456` when applicable.

## Coding conventions

- **Python 3.9+** — use only features that work across 3.9–3.12.
- **Type hints on new public APIs.** Not required to retrofit existing
  untyped code in the same PR.
- **No new runtime dependencies without discussion.** The current tree is
  intentionally three deps (`requests`, `rich`, `python-dotenv`).
- **Errors are user-facing.** Every raised `RuntimeError` in the auth /
  integration paths should include enough context (URL, header, hint) that
  a user can act on it. See `jira_client._auth_hint()` for the shape.
- **No feature-flags or backwards-compat shims** unless there's a real
  external consumer. This is a CLI tool; breaking flag changes are okay
  before v1.0 as long as they're in the CHANGELOG.

## Release process

Releases are cut by the maintainer:

1. Bump `__version__` in `watcher/__init__.py`.
2. Add a `## [x.y.z]` section to `CHANGELOG.md`.
3. Commit + merge via PR.
4. Tag on `main`: `git tag v0.2.0 && git push origin v0.2.0`.
5. GitHub Actions builds and publishes to PyPI.

The tag must match `__version__` and must point at a commit on `main`;
otherwise the publish workflow fails.

## Code of Conduct

By participating in this project you agree to abide by the
[Code of Conduct](CODE_OF_CONDUCT.md).
