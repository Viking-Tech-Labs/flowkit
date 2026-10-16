# flowkit

A working flow for Claude Code, shipped as versioned plugins from one marketplace. You install
what you want, an update reaches you when a plugin's version changes, and your own data (your
notes, your `CLAUDE.md`) stays yours, apart from the kit. Nobody clones this repository to use it.

## Quick start

Paste this into Claude Code and it does the setup:

> Add the plugin marketplace `Viking-Tech-Labs/flowkit` with `claude plugin marketplace add`,
> install the `brain` plugin from it with `claude plugin install brain@flowkit`, then tell me to
> type `/reload-plugins` and `/brain:init ~/brain`.

Or by hand:

1. Add the marketplace: `claude plugin marketplace add Viking-Tech-Labs/flowkit`
2. Install the plugin: `claude plugin install brain@flowkit`
3. In Claude Code (a new session, or `/reload-plugins` in a running one): `/brain:init ~/brain`

While this repository is private, step 1 needs access to the Viking-Tech-Labs organisation on
GitHub plus a logged-in `gh` or an SSH key; the marketplace add then uses those credentials, with
no prompt. Step 3 creates your vault at `~/brain` (any path works) from the template inside the
plugin, starts git there on `main`, and writes the vault's `.claude/settings.json`, which enables
the plugin for every session opened in the vault. Then fill in `_me.md` in the vault by hand:
every session there reads it first.

`claude plugin install` takes `--scope user` (default), `project` or `local`. To try the kit in
one repository without touching your user settings, add the marketplace and install with
`--scope project` there.

## The plugins

| Plugin | What it gives you | State |
|---|---|---|
| `brain` | A personal knowledge wiki in plain markdown: `/brain:init`, `/brain:save`, `/brain:recall`, `/brain:lint`, `/brain:inbox`. Symptom-first lookups, a trust stamp on every runbook. | 0.1.0, usable |
| `session-discipline` | Session conventions you adopt into your own `CLAUDE.md` as one block between markers (`/session-discipline:adopt`, with `status` and `update`): each rule says what it replaces, you delete the ones you do not want. Plus a decision-interview skill. | 0.1.0, usable |
| `prompt-audit` | Reads the prompts you typed since the last audit, finds what you keep asking for by hand (`/prompt-audit:audit`), interviews you on the fixes and writes the accepted rules into your own section of `CLAUDE.md`. Your prompts never leave a scratch directory. | 0.1.0, usable |
| `walkthrough` | Makes you understand a branch, a pull request or a pasted diff by predicting it first (`/walkthrough:start`): a summary, then questions on what happens underneath, graded against the code. Where your sound expectation and the code differ is a finding, written up as a small change you can reject. Unattended, it gives the steps to try by hand. | 0.1.0, usable |
| `handoff` | Closes a session so the next one starts cold (`/handoff:close`): commits green work, writes the pick-up block into the repo's plan doc, drafts the next session's prompt when repo work is left, records the close in the story's file. | 0.1.0, in progress |

Each plugin is its own decision: install only the ones you want, with
`claude plugin install <plugin>@flowkit`. Each says in its own README what it needs.

## Updates

An update reaches you only when a plugin's `version` changes; commits in between do not. You get
a release in one of three ways:

- **Auto-update**, off by default for a marketplace you add yourself. Turn it on in Claude Code:
  `/plugin`, then Marketplaces, select `flowkit`, Enable auto-update. It runs after the first
  message of a session with a random delay of up to ten minutes. The session that is already
  running keeps the version it loaded; the update applies to the next session, or after
  `/reload-plugins`. The settings equivalent is `"autoUpdate": true` next to `source` on the
  marketplace's entry under `extraKnownMarketplaces` in your settings.
- **By hand, the whole marketplace**: `claude plugin marketplace update flowkit`.
- **By hand, one plugin**: `claude plugin update brain@flowkit`. There is no update-all command.

## Your tracker

flowkit names no issue tracker or code host: add your own MCP servers or skills for Jira, Linear,
GitHub Issues or whatever you use, and a task that mentions a story or ticket reaches it through
those.

## Maintaining

A release is: change the files, bump `version` in that plugin's `.claude-plugin/plugin.json`
(only there; do not also set it in `marketplace.json`, the two would disagree), tag, push. With a
version set, Claude Code keeps the cached copy no matter how many commits are pushed, so a change
without a bump reaches nobody. A marketplace added from a local directory
(`claude plugin marketplace add <path to a checkout>`) loads the files in place and ignores
`version`, which is convenient while developing and misleading when testing an update.

```
.claude-plugin/marketplace.json   the catalog: the five plugins, by relative path
plugins/<name>/                   one directory per plugin, each with .claude-plugin/plugin.json
plugins/brain/skills/             the five skills; scripts/ holds brain-lint.py and vault.py
plugins/brain/template/           the vault /brain:init creates
plugins/session-discipline/       block/conventions.md, the adopt and decision-interview skills, scripts/adopt.py
plugins/prompt-audit/             the audit skill and its analyst brief, scripts/extract.py and rules.py
plugins/walkthrough/              the start skill and its question guide, scripts/history.py
plugins/handoff/                  the close, away, back and setup skills, hooks/hooks.json, scripts/
```
