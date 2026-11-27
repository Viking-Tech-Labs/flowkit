---
name: setup
description: Offer the handoff status line, which shows each session's model, context used and cost, and records them so closes and ladders can read them, choose how spawned sessions start (tmux or Claude's background sessions), whether finished story tabs close on their own, whether a session an API error stopped carries on while the user is away, whether one a usage limit stopped is continued after the reset, whether the user gets reminders of what waits on them while away from the computer, what a chain's last slice leaves behind (the branch, a Draft MR, the MR ready, merged), whether a helper session reports its verdict back to the session that launched it, a starter capacity file (how many chains at once, what a repository has only one of), and on macOS a timer that keeps the queue, the watches and the reminders going with tmux detached or absent; `/handoff:setup remove` takes away what the setups installed. Shows each change to the user's settings as a diff and writes it only on a yes. Use when the user types /handoff:setup, asks how a close knows the context or cost of a session, or wants spawned sessions to run without tmux.
argument-hint: [remove]
disable-model-invocation: true
---

Arguments: `$ARGUMENTS`

Set up what the handoff plugin needs from the user's own settings. A plugin cannot change those
settings itself; this skill shows the change and makes it only when the user says yes.

The helper is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py"` (`handoff.py` below).

When the arguments are `remove`, do only "Removing what the setups installed", at the end.

## The status line

Why it is offered: a close is due at about 40% of the context window, and a ladder's log wants
each session's cost. Claude Code hands those numbers only to the status line, so this one records
them in `~/.claude/session-stats/<session id>.json` (and `by-name/<session name>.json` for a named
session) on every refresh, and shows a short line: model, effort, context used, cost. It also
keeps the plan's weekly usage in `~/.claude/usage-stats/` (a `week-*.json` per week and login, and
`series.jsonl`), which `flowkit-usage` falls back to when Claude Code does not answer.

1. Run `handoff.py statusline diff "${CLAUDE_PLUGIN_ROOT}"`. If it says `installed`, say so and
   stop.
2. Show the user the diff as it printed it, and say in plain words what changes: the script is
   copied to `~/.config/flowkit/statusline.py` (kept at the plugin's version at every session
   start), and the settings' `statusLine` runs it. If they already have a status line, theirs
   keeps showing: the new command records the numbers and then runs theirs (`--then`). Mention
   that the settings file is rewritten with two-space indentation, as the diff shows, and that a
   backup is kept next to it as `settings.json.flowkit-bak`.
3. Ask with AskUserQuestion: install (recommended), or leave the settings as they are. Without
   the AskUserQuestion tool (a headless run), do not install: print the diff and the command
   that would install it, and stop.
4. On a yes, run `handoff.py statusline install "${CLAUDE_PLUGIN_ROOT}"` and relay its lines: the
   status line applies from the next session. On a no, write nothing.

To undo it, the user restores the backup or removes `statusLine` from their settings.

## The launch driver

How `/handoff:close spawn` and `/handoff:spawn` start the next session, the `driver` key of
`~/.config/flowkit/handoff.conf` (`handoff.py state` prints it as `spawn-driver`):

- `tmux`: each story gets a tab and its sessions are tiled panes. For people who live in tmux;
  `/handoff:setup-tmux` adds the kit.
- `background`: `claude --bg`, Claude's own background sessions. No tmux needed: the sessions
  show in `claude agents`, the board (`/board`) and the Claude app on the phone (Remote Control),
  and `claude attach <name>` opens one in a terminal. A background session that sits idle at
  its prompt for about an hour is stopped by Claude (its conversation is kept); the story's
  reporter is woken again by the next close. A folder never opened in Claude refuses a launch
  until the user runs `claude` there once and accepts the trust prompt.

A `spawn-driver` that is neither is an outside driver the user set by hand (an executable
`flowkit-driver-<name>`, `docs/drivers.md` in the flowkit repository): say which one it is and
leave this part alone, unless the user asks to go back to one of the two.

1. Ask with AskUserQuestion which driver, the current one marked; recommend `tmux` when `$TMUX`
   is set, else `background`. Without the AskUserQuestion tool, change nothing: print the current
   driver and the command below, and stop.
2. Write the answer: `handoff.py conf driver <tmux|background>`. Other keys of the file stay.
3. With `background`, offer two settings of the user's own, each only with consent, as one
   AskUserQuestion with both options (multiSelect, both recommended):
   - `remoteControlAtStartup=true`: every session starts with Remote Control on, also the ones
     the user starts from agent view or by hand, so the phone reaches them all. Spawned sessions
     get it anyway (`--remote-control`). Where an organisation turns Remote Control off, the
     sessions still run and `claude attach <name>` is the way in.
   - `worktree.bgIsolation="none"`: a session dispatched from agent view (`claude agents`)
     otherwise gets its own git worktree and commits, pushes and opens a draft pull request on
     its own; `none` keeps it in the repo like any other session. Sessions `flowkit-spawn` starts
     are not isolated either way.
   Show `handoff.py settings diff <chosen key=json pairs>` as it prints, then write with
   `handoff.py settings install <the same pairs>` (a backup is kept as
   `settings.json.flowkit-bak`). Values are JSON: `remoteControlAtStartup=true`,
   `'worktree.bgIsolation="none"'`. Nothing chosen: write nothing.
4. Say what is left to the user: push notifications for sessions that need them are the
   `/config` toggles plus the Claude app signed in on the phone.

## The timer

Some of the plugin's work happens between prompts, in a pass every 15 seconds (the plugin README's
"The tick"): the capacity queue drains, a watch poller or a usage-limit waiter that died starts
again, the reminders go out, the tmux tabs get their words. Without a timer the tmux kit's status
bar runs that pass, so it stops while no tmux client is attached and never runs for someone on the
`background` driver without tmux. On macOS a LaunchAgent runs it instead.

1. Run `handoff.py timer diff "${CLAUDE_PLUGIN_ROOT}"`. If it says `installed`, say so and go on
   to the next part. If it says `not on this system`, relay that line and go on: there is nothing
   to install.
2. Show the user what it printed, and say in plain words what changes: four commands are copied to
   `~/.local/bin/` (`flowkit-tick`, `flowkit-tmux`, `flowkit-away`, `flowkit-usage`, kept at the
   plugin's version at every session start), and `~/Library/LaunchAgents/flowkit.tick.plist` is
   written and loaded. It runs `flowkit-tick --timer` every 15 seconds from login on, with this
   session's `PATH` and, where they are set, `CLAUDE_CONFIG_DIR`, `XDG_CONFIG_HOME` and `LANG`
   written into it, and it ticks the config folder this session runs in. `installed with other
   values` means the file on disk carries another PATH or other folders (the diff shows which):
   installing writes this session's. `written, not loaded` means the file is right and launchd
   does not run it: installing loads it.
3. Ask with AskUserQuestion: install (recommended), or leave it. Without the AskUserQuestion tool
   (a headless run), write nothing: print what step 1 printed and the command that would install
   it, and stop.
4. On a yes, run `handoff.py timer install "${CLAUDE_PLUGIN_ROOT}"` and relay its lines; then run
   `~/.local/bin/flowkit-tick status`, which says `kept by the timer` once the first pass is
   through (a few seconds). When launchctl refuses (a sandboxed shell may), the error names the
   `launchctl bootstrap` command: hand it to the user to run in a terminal. On a no, write nothing.

To undo it: `/handoff:setup remove`.

## Capacity

`flowkit-spawn` holds every story launch to a number of chains at once, and leases the things a
repository has only one of (the plugin README's "Capacity"). The numbers live in
`${XDG_CONFIG_HOME:-~/.config}/flowkit/capacity.conf`; with no file a machine runs 3 chains and
declares no resources. This part writes a starter file from two answers. The helper is
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/capacity.py"` (`capacity.py` below).

1. If that file exists, say so, name it and change nothing: its lines are the user's. Stop this
   part.
2. Ask with AskUserQuestion, both questions in one call. Without the AskUserQuestion tool, write
   nothing: print the command of step 3 with the defaults, and stop.
   - How many chains this machine runs at once: 2, 3 (recommended, what it runs with no file) or
     4; a number of their own through Other.
   - What this repository has only one of that a session uses (multiSelect): an iOS simulator,
     an Android emulator, a local backend, nothing; a dev-server port or anything else through
     Other. Skip this question outside a git repository.
3. Write it: `capacity.py init --slots <n>`, and for each thing they chose `--repo "$PWD"` once
   and a `--resource '<name>=<what a session does with it>'` each. The name is one word
   (`simulator`, `emulator`, `backend`); the text is one line a session reads before it takes
   the resource, such as `install a build, run a device check, hand over a test` or `start the
   local stack; release once it is stopped`. Relay what it prints: the file, line by line. It
   never changes a file that exists.
4. Say what the file can hold beyond the starter, for the user to add by hand from the README's
   "Capacity": other repositories' sections, a repo's own `slots`, `checkouts = 1`, an `audit`
   pattern and a `stop` command per resource, weights per kind of work. And that a slice asks
   for a resource in its prompt's header, `Needs: simulator`.

## Closing finished story tabs

With the `tmux` driver and the tmux kit, the `autoclose` key of `handoff.conf` lets the tick
close a story's tab once the story is finished (the plugin README's "The tmux
kit" says when). It kills sessions, so it is off until the user turns it on.

1. Skip this part when the driver is `background`.
2. Say in plain words what it does: the whole story goes at once, reporter last, only once its
   final report is in (or every launched session has closed, with no reporter left), nothing in
   it is busy, waiting, watching or in error, and every session has been still for 5 minutes;
   `tmux set-option -w @flowkit_hold 1` in a tab keeps it; the adhoc tab and the user's own
   windows are never closed.
3. Ask with AskUserQuestion: turn it on (recommended), or leave it off. Without the
   AskUserQuestion tool, change nothing: print the command below, and stop.
4. Write the answer: `handoff.py conf autoclose <on|off>`. The next tick, within 15 seconds,
   applies it; nothing needs a restart.

## Continuing after an API error

The `autoretry` key of `handoff.conf` lets a session that an API error stopped carry on by
itself while the user is away (the plugin README's "API errors" says how). It types into the
user's sessions, so it is off until the user turns it on.

1. Say in plain words what it does: only while `/handoff:away` or `/handoff:awake` is set,
   and only on `server_error` and `overloaded`, one waiter per session waits 2, 5, 10, 20 and
   30 minutes, then every 30, for up to 12 hours, and types one line asking the session to carry
   on, never while the session is busy, its pane is in copy mode or holds a draft; a background
   session is resumed with that line instead; an output-token-limit stop gets one continue asking
   for smaller pieces. Without it, the error is still recorded and the tab labelled `error`.
2. Ask with AskUserQuestion: turn it on (recommended), or leave it off. Without the
   AskUserQuestion tool, change nothing: print the command below, and stop.
3. Write the answer: `handoff.py conf autoretry <on|off>`. The next error reads it; nothing needs
   a restart.

## Continuing after a usage limit

The `limitresume` key of `handoff.conf` is on unless the user turns it off: a session that a
usage limit stopped is continued three minutes after the limit resets, whether the user is away
or not (the plugin README's "Usage limits" says how, and why Claude Code's own wait does not
cover sessions with Remote Control on). Nothing to install.

1. Say in plain words what it does: one waiter per session waits for the reset, keeps the
   computer awake only for the last 30 minutes, and types one line asking the session to carry
   on, never while the session is busy, went on by itself, shows a dialog, holds a draft or is
   in copy mode; after three continues that end on the limit again it stops and says so in the
   story file. The user's own `autoContinueAtUsageLimit: false` in `settings.json` turns it off
   too.
2. Ask only when the user brings it up or `handoff.conf` already says `limitresume=off`: keep it
   on (recommended), or turn it off. Without the AskUserQuestion tool, change nothing: print the
   command below, and stop.
3. Write the answer: `handoff.py conf limitresume <on|off>`. The next stop reads it, and a waiter
   already waiting reads it before it types; nothing needs a restart.

## Clearing away at the keyboard

The `idle_release` key of `handoff.conf` lets a prompt the user types at the computer clear an
away flag they forgot (the plugin README's "Back at the keyboard" says how). It ends a state the
user set, so it is off until they turn it on, and it needs macOS.

1. Say in plain words what it does: once the away flag has been in effect for 30 minutes, the
   first prompt typed at the computer (a key or the mouse was used in the last two minutes) clears
   it, and every session hears so; a prompt from the phone, a task's notice or another session's
   message clears nothing. Without it the flag stays until `/handoff:back` or its 12 hours.
2. Ask with AskUserQuestion: turn it on (recommended on a Mac they also work at), or leave it
   off. Without the AskUserQuestion tool, change nothing: print the command below, and stop.
3. Write the answer: `handoff.py conf idle_release <on|off>`. Where nothing reports the idle time
   (anything but macOS) `on` is refused: relay why. The next prompt reads it; nothing needs a
   restart.

## Reminders

The `remind` key of `handoff.conf` lets the tick push what waits on the user while
they are neither away nor at the computer (the plugin README's "Reminders" says what and how).
It types into a reporter's pane, so it is off until the user turns it on.

1. Skip this part when the driver is `background` and `handoff.py timer diff
   "${CLAUDE_PLUGIN_ROOT}"` does not say `installed`: without tmux only the timer runs the tick
   that sends reminders.
2. Say in plain words what it does: at most once a minute, silent while `/handoff:away` is in
   effect or while the user touched the keyboard or mouse in the last 10 minutes; at once, what
   Claude Code's own pushes cannot see (a pane parked at a start-up prompt, a usage-limit stop
   flowkit cannot continue, a test item ready for a device test), and every 2 hours, when the set
   changed, a digest with the sessions and test items waiting on them. The push goes through the
   oldest idle story reporter in a tmux pane (`reporter=on`), else a `notify` command of theirs,
   else a macOS notification; the text is kept in `~/.claude/flowkit/remind-latest.md`. A
   coordinator of theirs that keeps its own reminder timer should drop it, or both push.
3. Ask with AskUserQuestion: turn it on (recommended when they use the tmux kit and leave chains
   running), or leave it off. Without the AskUserQuestion tool, change nothing: print the command
   below, and stop.
4. Write the answer: `handoff.py conf remind <on|off>`. With `on`, offer the `notify` key only
   when the user names a push service of their own: `handoff.py conf notify '<command>'`, a shell
   command that gets the line on its stdin (`curl -s -d @- ntfy.sh/<their topic>`). The next tick
   reads both; nothing needs a restart. `handoff.py remind --dry-run` shows what a pass would say
   now.

## Where a chain ends

The `chain_end` key of `handoff.conf` is what a close proposes when it drafts a story's first
prompt: what the chain's last slice leaves behind. The close asks once per story (the user's own
words are taken as they are), writes the answer as a `Chain end:` line into the story file's
header, and every brief of the chain repeats it next to `Stop when:`, the last slice told it is
the last; unattended, the close takes this value "(auto)". Until it is set, `mr-ready` is
proposed.

| Value | Every slice | The last slice |
|---|---|---|
| `branch` | commits, never pushes | leaves the branch committed; the user pushes; no watch |
| `draft-mr` | pushes its green commits; the first push opens a Draft MR | pushes, registers the watch and closes; the MR stays in Draft; a red pipeline gets one fix slice |
| `mr-ready` | as `draft-mr` | as `draft-mr` with `--ready`: the watch takes the MR out of Draft on green; no merge |
| `merge` | as `draft-mr` | takes the MR out of Draft, sets auto-merge, registers the watch `--until merged` and closes; the watch reports the merge and the target branch's pipeline |

No session waits for a pipeline: the last slice's close registers a watch (`flowkit-watch`, the
close skill's step 9a), a script polls it, and green or red comes back as a section of the story
file. A slice in the middle of a chain reads the pipeline of the one before it and fixes a red it
caused.

1. Say in plain words what it does, with the table; `handoff.py state` prints the current value as
   `chain-end:`.
2. Ask with AskUserQuestion: the four values, the current one marked, `mr-ready` recommended.
   Without the AskUserQuestion tool, change nothing: print the command below, and stop.
3. Write the answer: `handoff.py conf chain_end <value>`. The next close that drafts a story's
   first prompt proposes it; nothing needs a restart.

## A helper reports back

The `report_to` key of `handoff.conf` lets a session launched beside a story's chain (a review or
investigation `/handoff:spawn` starts, or one a close launches next to its chain; never the
chain's next slice) send its verdict back to the session that launched it: its brief gets a
`Report-to: <session>` line in its header, and its close sends that session one message (the
plugin README's "A helper reports back"). It is off until the user turns it on: the launching
session then has a message to relay and act on.

1. Say in plain words what it does: with `on`, `/handoff:spawn` with a prompt and a close's
   helper launch write `Report-to:` naming the launching session (`handoff.py self` prints its
   name); at its close the helper sends that session its verdict, one line, the findings by
   number with a worth-fixing word, the links, at most ten lines; the launching session relays
   it to the user and acts on it as their rules say. A message between sessions of different
   permission modes waits for the receiver's approval, one to a session parked on a question
   waits with it, and a stopped background session gets none, so the helper's close and DONE
   carry the verdict too. A name instead of `on` sends every helper's verdict to that session;
   `report_to=none` on a `/handoff:spawn` skips one launch, and a `single` without a prompt never
   carries the line.
2. Ask with AskUserQuestion: turn it on (recommended), or leave it off. Without the
   AskUserQuestion tool, change nothing: print the command below, and stop.
3. Write the answer: `handoff.py conf report_to <on|off>`. The next launch reads it; nothing
   needs a restart.

## Removing what the setups installed

Only when the arguments are `remove`.

1. Run `handoff.py setup remove --dry-run` and show the user its lines: what would go (the timer,
   unloaded and deleted; the commands in `~/.local/bin`; the tmux kit's files and the
   `source-file` line in their tmux.conf, a backup kept beside it) and what stays (their
   `handoff.conf`, `capacity.conf`, ladder and status line). `setup: nothing to remove` ends it.
2. Ask with AskUserQuestion: remove it all, or leave everything. Without the AskUserQuestion tool,
   change nothing: print the lines and the command below, and stop.
3. On a yes, run `handoff.py setup remove` and relay its lines. A running tmux keeps the kit's
   settings until it restarts, and its status bar goes quiet. Uninstalling the plugin itself is
   theirs: `claude plugin uninstall handoff@flowkit`.
