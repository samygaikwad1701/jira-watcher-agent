"""jira-watcher-agent — AI-powered JIRA planner, organizer, and triage CLI.

Semantic versioning (semver.org):
    MAJOR.MINOR.PATCH
      MAJOR  incompatible API / CLI changes (breaking).
      MINOR  new functionality, backwards-compatible.
      PATCH  backwards-compatible bug fixes.
Pre-releases append `-alpha.N`, `-beta.N`, or `-rc.N`.

This is the single source of truth for the package version. `pyproject.toml`
reads it via `[tool.setuptools.dynamic] version = { attr = "watcher.__version__" }`,
and the CLI's `--version` flag prints it too. Bump this string per release,
tag the git commit with `v<same-string>`, then run `python -m build`.
"""

__version__ = "0.1.0"
