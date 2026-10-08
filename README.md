# flowkit

A working flow for Claude Code, shipped as versioned plugins from one marketplace, plus a thin
vault template for the part that is yours: your notes. Install what you want, update when a
plugin's version changes, keep your own data apart from the kit.

| Plugin | What it gives you | State |
|---|---|---|
| `brain` | A personal knowledge wiki in plain markdown: `/brain:save`, `/brain:recall`, `/brain:lint`, `/brain:inbox`, `/brain:init`. Symptom-first lookups, a trust stamp on every runbook. | 0.1.0, usable |
| `session-discipline` | Session conventions you adopt into your own `CLAUDE.md`, and a decision-interview skill. | 0.1.0, name reserved, content coming |
| `prompt-audit` | Mines your own prompt history for what you keep asking by hand and turns it into rules. | 0.1.0, name reserved, content coming |
| `walkthrough` | A guided test walkthrough of a branch, a pull request or a pasted diff. | 0.1.0, name reserved, content coming |

Each plugin is its own decision: install only the ones you want. Each says in its own README what
it needs.

## Install

The repository is not published yet, so until it is, clone it to a path of your choice (the
commands below use `~/flowkit`) and add the marketplace from that directory. Once it is published
the first command takes the repository's address instead.

1. Add the marketplace: `claude plugin marketplace add ~/flowkit`
2. Install the plugin: `claude plugin install brain@flowkit`
3. Create your vault from the template, then register it from inside Claude Code:

   ```sh
   cp -R ~/flowkit/template ~/brain
   git -C ~/brain init -b main && git -C ~/brain add -A && git -C ~/brain commit -m "Start vault"
   cd ~/brain && claude
   ```

   and in the session, `/brain:init`. Fill in `_me.md` in the vault by hand: every session in the
   vault reads it first.

`claude plugin install` takes `--scope user` (default), `project` or `local`. To try the kit in
one repository without touching your user settings, add the marketplace and install with
`--scope project` there.

`template/` is the vault. It is yours after the copy: `CLAUDE.md`, `_me.md`, `index.md`, `log.md`,
`hot.md`, `inbox.md`, `templates/`, `wiki/`, `raw/`. Its `CLAUDE.md` is deliberately thin: it says
what the files are and which skill applies when, and the protocol itself stays in the plugin so
that it can be updated. The template does not yet ship a `.claude/settings.json` that registers
the marketplace for everyone who opens the vault: that file needs the marketplace's published
address, so it arrives with the first release.

## Updates

**An update reaches users only when a plugin's `version` changes.** Every plugin sets `version`
in its `.claude-plugin/plugin.json`, and with a version set, Claude Code keeps the cached copy no
matter how many commits are pushed. So a release is: change the files, bump `version` in that
plugin's `plugin.json` (only there; do not also set it in `marketplace.json`, the two would
disagree), push. A marketplace added from a local directory loads the files in place and ignores
`version`, which is convenient while developing and misleading when testing an update.

Users get a release in one of three ways:

- **Auto-update**, off by default for a marketplace you add yourself. Turn it on in Claude Code:
  `/plugin`, then Marketplaces, select `flowkit`, Enable auto-update. It runs after the first
  message of a session with a random delay of up to ten minutes. The session that is already
  running keeps the version it loaded; the update applies to the next session, or after
  `/reload-plugins`. The settings equivalent is `"autoUpdate": true` next to `source` on the
  marketplace's entry under `extraKnownMarketplaces` in your settings.
- **By hand, the whole marketplace**: `claude plugin marketplace update flowkit`.
- **By hand, one plugin**: `claude plugin update brain@flowkit`. There is no update-all command.

## Your tracker

flowkit names no issue tracker or code host: add your own MCP servers or skills for Jira, Linear, GitHub Issues or whatever you use, and a task that mentions a story or ticket reaches it through those.

## What is in the repository

```
.claude-plugin/marketplace.json   the catalog: the four plugins, by relative path
plugins/<name>/                   one directory per plugin, each with .claude-plugin/plugin.json
plugins/brain/skills/             the five skills; scripts/ holds brain-lint.py and vault.py
template/                         the vault to copy
```
