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
(`claude plugin test`). The full run takes several minutes; a few `handoff` tests time things, so
run it on a quiet machine and run a single failure again before blaming the code. The `tests`
workflow runs the same script on macOS and Linux for every branch push and pull request.

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
