<!--
Thanks for contributing! Please make sure your PR checks the boxes below.
Delete sections that don't apply.
-->

## Summary

<!-- One or two sentences: what does this PR change and why? -->

## Type of change

- [ ] Bug fix (non-breaking)
- [ ] New feature (non-breaking)
- [ ] Breaking change (bumps MAJOR — CHANGELOG updated)
- [ ] Documentation only
- [ ] Refactor / internal cleanup (no user-visible change)

## How was this tested?

<!--
For CLI changes, paste the actual terminal output. For integration
changes, describe which JIRA / Bitbucket / Jenkins / GitHub / EKS
account you tested against.
-->

## Checklist

- [ ] `ruff check watcher/` passes
- [ ] `ruff format --check watcher/` passes
- [ ] `python -m compileall -q watcher/` passes
- [ ] `watcher --version` prints the expected version
- [ ] `CHANGELOG.md` updated under `## [Unreleased]` if this is user-visible
- [ ] No new runtime dependencies were added (or discussed in the linked issue)
- [ ] No secrets, `.env`, or memory files added to the diff

## Related issues

Fixes #
