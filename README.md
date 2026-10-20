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

### Chains of sessions

`handoff`, `board`, `ladder` and `coordinate` let a session close and start the next one, overnight
included, with a board to watch them. Update Claude Code first (`claude update`; tested on
2.1.302) and have `python3`. For the terminal layout install tmux (`brew install tmux`) and run
Claude inside it; for a UI, skip tmux and choose `background` in step 3.

1. In a terminal: `claude plugin marketplace update flowkit` (a marketplace added before these
   plugins existed does not list them until it is updated), then `claude plugin install
   handoff@flowkit` and the same for `board@flowkit` and `ladder@flowkit` (and
   `coordinate@flowkit` if your stories span several repositories).
2. Open a new Claude Code session, or type `/reload-plugins`.
3. `/handoff:setup`: offers the status line (closes read its context and cost numbers) and asks
   how new sessions start, tmux or background sessions you watch on the board and your phone.
4. tmux only: `/handoff:setup-tmux` shows the one line it adds to your tmux config; the cheat
   sheet lands in `~/.config/flowkit/`.
5. `/ladder:setup` picks a model and effort per kind of session from your plan; `/ladder:edit`
   changes it later.

Then finish a session with `/handoff:close` (`/handoff:close spawn` also starts the next one),
open `/board` to see your stories, and type `/handoff:away` before you leave a chain running
and `/handoff:back` when you return. A closed laptop lid pauses everything.

## The plugins

| Plugin | What it gives you | State |
|---|---|---|
| `brain` | A personal knowledge wiki in plain markdown: `/brain:init`, `/brain:save`, `/brain:recall`, `/brain:lint`, `/brain:inbox`. Symptom-first lookups, a trust stamp on every runbook. | 0.1.0, usable |
| `session-discipline` | Session conventions you adopt into your own `CLAUDE.md` as one block between markers (`/session-discipline:adopt`, with `status` and `update`): each rule says what it replaces, you delete the ones you do not want. Plus a decision-interview skill. | 0.1.0, usable |
| `prompt-audit` | Reads the prompts you typed since the last audit, finds what you keep asking for by hand (`/prompt-audit:audit`), interviews you on the fixes and writes the accepted rules into your own section of `CLAUDE.md`. Your prompts never leave a scratch directory. | 0.1.0, usable |
| `walkthrough` | Makes you understand a branch, a pull request or a pasted diff by predicting it first (`/walkthrough:start`): a summary, then questions on what happens underneath, graded against the code. Where your sound expectation and the code differ is a finding, written up as a small change you can reject. Unattended, it gives the steps to try by hand. | 0.1.0, usable |
| `handoff` | Closes a session so the next one starts cold (`/handoff:close`): commits green work, writes the pick-up block into the repo's plan doc, drafts the next session's prompt when repo work is left, records the close in the story's file; `spawn` starts that session in the story's tmux tab (`/handoff:spawn` starts any other), `/handoff:setup-tmux` offers a beginner-friendly tmux kit, and `/handoff:setup` can start sessions in the background instead (`claude --bg`, no tmux). `/handoff:away` makes every session, running or not, decide unattended until `/handoff:back`. | 0.3.1, usable |
| `board` | A live board of your story chains (`/board`): a card per story that is running, needs you, has stalled or is done, its FOR ME steps with ticks that persist, its merge-request links; `/where` and `/forme` print the same as text. Reads what `handoff` writes. | 0.1.1, usable |
| `ladder` | A fixed model ladder for `handoff` closes (`/ladder:setup`, `/ladder:edit`): a kind → model/effort table outside the plugin with defaults from your plan, and an effort gate that asks before a `max` launch once you turn `max` on. | 0.1.0, usable |
| `coordinate` | Makes a session the coordinator of a story across several repos or branches (`/coordinate:start <mission>`): plans one worker per workstream from a mission doc, starts them through `handoff`'s spawn with their briefs, routes their reports and keeps one status board; never implements. Needs `handoff`. | 0.1.0, usable |

Each plugin is its own decision: install only the ones you want, with
`claude plugin install <plugin>@flowkit`. Each says in its own README what it needs.

## GitHub Copilot

Copilot reads this marketplace as it is, in Copilot CLI and in VS Code. Three plugins work there:
`brain`, `session-discipline` and `walkthrough`. The other five need Claude Code: `prompt-audit`
reads Claude Code's transcripts, and `handoff`, `board`, `ladder` and `coordinate` drive the
`claude` command. Checked on 2026-10-19 with Copilot CLI 1.0.100: the marketplace adds, the three
plugins install and all eight of their skills load (from brain 0.1.1 and session-discipline 0.1.1).
Running them in a session, and VS Code, are not yet tried. The access note under Quick start
applies here too.

Copilot CLI is the closer fit. Its docs confirm this plugin format, the `${CLAUDE_PLUGIN_ROOT}` and
`${CLAUDE_PLUGIN_DATA}` variables every skill passes to its script, and a tool that asks you
questions.

1. `copilot plugin marketplace add Viking-Tech-Labs/flowkit`
2. `copilot plugin install brain@flowkit`, and the same for `session-discipline@flowkit` and
   `walkthrough@flowkit`
3. In a new `copilot` session: `/brain:init ~/brain`, then
   `/session-discipline:adopt ~/.copilot/copilot-instructions.md`. Copilot CLI does not read
   `~/.claude/CLAUDE.md`, so the conventions go into its own user-level instructions file.
   `/skills list` shows the names Copilot gave the skills (it lists them without the plugin name).
4. Updates: `copilot plugin marketplace update flowkit`, then `copilot plugin update --all`.

In VS Code:

1. In your user settings: `"chat.plugins.enabled": true`,
   `"chat.plugins.marketplaces": ["Viking-Tech-Labs/flowkit"]` (experimental) and
   `"chat.useClaudeMdFile": true`, which makes chat read `~/.claude/CLAUDE.md`, where
   `/session-discipline:adopt` writes by default.
2. Install the three from the Extensions view (search `@agentPlugins`) or with the command
   Chat: Install Plugin From Source and `https://github.com/Viking-Tech-Labs/flowkit`.
3. In an agent chat: `/brain:init ~/brain`, then `/session-discipline:adopt`.

VS Code's docs do not say whether it fills in the two variables or has a question tool. If
`/brain:init` creates the vault and then fails to save its path, put `export BRAIN_DIR=~/brain` in
your shell profile and restart VS Code: every brain command reads that first. If a command cannot
find its script at all, use Copilot CLI. Without a question tool the decision interview asks in
plain chat text.

Chains of sessions stay Claude Code only. Copilot CLI has no machine-readable list of sessions
and their state and no detached background sessions, and `handoff` and `board` are built on both.

### Setting Copilot CLI up like the Claude Code flow

What each piece of the Claude Code flow is in Copilot CLI, and how to set it up. Flags, settings
and commands are from `copilot help` of 1.0.100 (settings go in `~/.copilot/settings.json`); the
hook is from the Copilot docs. None of it is tried in a session yet.

| Claude Code flow | Copilot CLI | Set up |
|---|---|---|
| session conventions in `~/.claude/CLAUDE.md` | `~/.copilot/copilot-instructions.md` | `/session-discipline:adopt ~/.copilot/copilot-instructions.md` |
| skills in `~/.claude/skills` | `~/.copilot/skills` (`~/.agents/skills` is read too) | `npx skills add <repo> -g -a github-copilot` |
| auto mode | the `assisted` permission mode (`allow-all` is `--yolo`) | `"defaultPermissionMode": "assisted"` |
| a model and effort per kind of session (`ladder`) | `--model`, `--reasoning-effort`, `/model` | `"model"` for the default; flags per launch |
| `/handoff:close spawn` starts the next session in a tmux pane | `copilot -i "<prompt>" --name <name>` in a new tmux window | a launcher script that reads the prompt file |
| Remote Control | `--remote`, `/remote` | the flag in the launcher |
| keep the Mac awake while sessions run | keep-alive | `"keepAlive": "busy"`, or `/keep-alive` |
| `/handoff:away`: sessions decide without asking | `--no-ask-user`, or a `preToolUse` hook denying `ask_user` | the flag in the launcher, or a hook in `~/.copilot/hooks/` |
| the board, which session needs you | the Sessions tab inside one `copilot` process; OS notifications | `"notifications": true` |
| the status line with context and cost | `/statusline`, `/context`, `/usage` | `/statusline` |
| resume a session | `--resume=<name>`, `--continue` | none |
| unattended overnight | `/delegate`: GitHub's cloud agent, a branch and a draft pull request | none |

Nothing in Copilot CLI stands in for `claude agents --json` (every session and its state, which
the board and `away` read), `claude --bg`, nudging a session parked on a question, or the prompt
audit, which reads Claude Code's transcripts.

To have Copilot do the setup, start `copilot` in your home folder and paste:

> Read ~/flowkit/README.md, the section "Setting Copilot CLI up like the Claude Code flow", and set
> up my Copilot CLI to match it: the settings in ~/.copilot/settings.json, a tmux launcher script
> that starts a named session from a prompt file with --remote, and anything else in the table.
> Show me each change before you make it.

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
.claude-plugin/marketplace.json   the catalog: the eight plugins, by relative path
plugins/<name>/                   one directory per plugin, each with .claude-plugin/plugin.json
plugins/brain/skills/             the five skills; scripts/ holds brain-lint.py and vault.py
plugins/brain/template/           the vault /brain:init creates
plugins/session-discipline/       block/conventions.md, the adopt and decision-interview skills, scripts/adopt.py
plugins/prompt-audit/             the audit skill and its analyst brief, scripts/extract.py and rules.py
plugins/walkthrough/              the start skill and its question guide, scripts/history.py
plugins/handoff/                  the close, spawn, away, back, setup and setup-tmux skills, hooks/hooks.json, scripts/, bin/flowkit-spawn and flowkit-tmux, tmux/ (the kit)
plugins/board/                    the hooks module (hooks/register.tsx draws the Pane, hooks/model.ts reads the stories), types/, tests/
plugins/ladder/                   the setup and edit skills, hooks/hooks.json (the effort gate), scripts/ladder.py, tests/
plugins/coordinate/               the start skill and its brief template, scripts/coordinate.py, tests/
```
