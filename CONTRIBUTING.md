# Contributing

Ideas and bugs are welcome as issues (the forms under **New issue**) and as threads in
Discussions. A pull request is welcome too; say in its description what it changes and how you
checked it.

## This repository is public

Nothing that names a real workplace goes into a tracked file, a commit message, a tag, an issue or
a discussion:

- no employer, client or colleague names, and no tracker accounts (an issue tracker's site or
  workspace, a code host's group);
- no machine paths (`/Users/<you>/...`) and no credentials, tokens or keys;
- no raw logs: cut them down to the lines that matter and replace the rest.

Use placeholders instead: `gitlab.com/g/p` for a project, `/Users/me/repo` for a path,
`example.com` for a host. An issue or a discussion is public the moment it is posted and only a
maintainer can remove it, so read it once more before you send it. The `gitleaks` workflow scans
every push for secret values.

## Running the tests

```
scripts/test.sh            # every plugin's suite, then the scripts' own
scripts/test.sh handoff    # one suite: a plugin's name, or `scripts`
```

It needs `python3`, plus `tmux` for the `handoff` suite and Claude Code for the `board` suite
(`claude plugin test`); the test of `flowkit-board` is skipped without `bun` or Node 22.18 or newer.
The full run takes several minutes; a few `handoff` tests time things, so
run it on a quiet machine and run a single failure again before blaming the code. The `tests`
workflow runs the same script on macOS and Linux for every branch push and pull request.

## Writing a driver

The `handoff` plugin starts sessions in tmux or in Claude Code's background service, and through
any executable named `flowkit-driver-<name>` for everything else (another multiplexer, a terminal
with a remote-control command). [`docs/drivers.md`](docs/drivers.md) is the contract: three
required requests (`open`, `list`, `type`), four optional ones (`screen`, `label`, `close`,
`pick`), and what flowkit does without each. It is experimental until a driver from outside this
repository passes.

- Start from `plugins/handoff/bin/flowkit-driver-screen`, the reference driver for GNU screen: copy
  it to `~/.local/bin/flowkit-driver-<name>` and replace the `screen` calls.
- Try it with `plugins/handoff/bin/flowkit-driver test <name>`: it asks every request with a
  harmless program in place of claude and exits non-zero when a required one fails. Then set
  `driver=<name>` in `~/.config/flowkit/handoff.conf` and launch one session with
  `flowkit-spawn --dry-run` first.
- A driver lives with its author: this repository ships the reference alone. What is welcome here
  is what your driver could not do with the requests as they are: an issue, with the request and
  what your program offers in its place.
- A change to the requests themselves touches `plugins/handoff/scripts/backend.py` (the class
  `Driver`), `docs/drivers.md`, `flowkit-driver` and the tests in
  `plugins/handoff/tests/test_backend.py` (`OutsideDriver`) and `test_driver.py`, together.

## How a release is made

An update reaches users only when a plugin's `version` changes. A maintainer releases with:

```
scripts/release.py <plugin> patch|minor --dry-run   # prints the steps, changes nothing
scripts/release.py <plugin> patch|minor
```

It refuses a dirty tree, red tests or an existing tag; then it bumps the version in the plugin's
`.claude-plugin/plugin.json`, commits, tags `<plugin>-v<version>`, fast-forwards `main` and pushes
both. README "Maintaining" has the details. A pull request does not bump versions; the release
does.
