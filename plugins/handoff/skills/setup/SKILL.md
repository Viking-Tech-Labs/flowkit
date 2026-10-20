---
name: setup
description: Offer the handoff status line, which shows each session's model, context used and cost, and records them so closes and ladders can read them, choose how spawned sessions start (tmux or Claude's background sessions), whether finished story tabs close on their own, and whether a session an API error stopped carries on while the user is away. Shows each change to the user's settings as a diff and writes it only on a yes. Use when the user types /handoff:setup, asks how a close knows the context or cost of a session, or wants spawned sessions to run without tmux.
disable-model-invocation: true
---

Set up what the handoff plugin needs from the user's own settings. A plugin cannot change those
settings itself; this skill shows the change and makes it only when the user says yes.

The helper is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py"` (`handoff.py` below).

## The status line

Why it is offered: a close is due at about 40% of the context window, and a ladder's log wants
each session's cost. Claude Code hands those numbers only to the status line, so this one records
them in `~/.claude/session-stats/<session id>.json` (and `by-name/<session name>.json` for a named
session) on every refresh, and shows a short line: model, effort, context used, cost.

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

## Closing finished story tabs

With the `tmux` driver and the tmux kit, the `autoclose` key of `handoff.conf` lets the kit's
status-bar tick close a story's tab once the story is finished (the plugin README's "The tmux
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

1. Say in plain words what it does: only while `/handoff:away` (or `~/.claude/awake`) is set,
   and only on `server_error` and `overloaded`, one waiter per session waits 2, 5, 10, 20 and
   30 minutes, then every 30, for up to 12 hours, and types one line asking the session to carry
   on, never while the session is busy, its pane is in copy mode or holds a draft; a background
   session is resumed with that line instead; an output-token-limit stop gets one continue asking
   for smaller pieces. Without it, the error is still recorded and the tab labelled `error`.
2. Ask with AskUserQuestion: turn it on (recommended), or leave it off. Without the
   AskUserQuestion tool, change nothing: print the command below, and stop.
3. Write the answer: `handoff.py conf autoretry <on|off>`. The next error reads it; nothing needs
   a restart.
