---
name: setup
description: Offer the handoff status line, which shows each session's model, context used and cost, and records them so closes and ladders can read them. Shows the change to the user's settings as a diff and writes it only on a yes. Use when the user types /handoff:setup, or asks how a close knows the context or cost of a session.
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
