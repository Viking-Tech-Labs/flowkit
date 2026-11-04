# flowkit

This repository is public. A tracked file, a commit message or a tag never names an employer, a
client, a colleague, a tracker account, a machine-local path or a credential; docs and tests use
placeholders (`gitlab.com/g/p`, `/Users/me/repo`). The workflow in `.github/workflows/` scans
every push for secret values; the maintainer's local git hooks scan for the rest. A hook that
blocks a commit or a push is right until proven otherwise: fix the content, never bypass it.
