---
name: setup
description: Set up the fixed model ladder that handoff closes read to pick the next session's model and effort - a kind to model/effort table with defaults from the user's plan, max off. Shows the file as a diff and writes it only on a yes. Use when the user types /ladder:setup, or asks how a close should choose the next session's model and effort.
disable-model-invocation: true
---

Write the ladder table that `/handoff:close` reads at its ladder step: for each kind of session
(planning, investigation, implementation, review, docs, config) a model and an effort. Nothing
in it moves on its own; the user changes it with `/ladder:edit`.

The helper is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ladder.py"` (`ladder.py` below).

1. Run `ladder.py show`. With `managed: no`, the user already has a ladder file of their own:
   say so, say it stays as it is and that the fixed table needs that file moved away first, and
   stop. With `managed: yes`, show the table and offer a reset to the plan's defaults in step 3
   instead of a first write.
2. Run `ladder.py plan`: it reads the user's plan (`claude auth status`) and prints its
   defaults. Max, Team and Enterprise get Opus for most kinds; Pro, an API key or an unknown plan
   get Sonnet for most. No row is at effort `max`, and `max` stays off.
3. Run `ladder.py diff --reset` and show the diff as it printed it. Say in plain words what it
   is: the file `~/.config/flowkit/ladder.md`, outside the plugin, so an uninstall or update
   never removes it; every close from now on takes the row of the next session's kind.
4. Ask with AskUserQuestion: write it (recommended), or change rows first (then follow
   `/ladder:edit` with the defaults as the base), or leave it. Without the AskUserQuestion tool
   (a headless run), write nothing: print the diff and the command that would write it, and stop.
5. On a yes, run `ladder.py write --reset` and relay its line. On a no, write nothing.

The effort gate: once the user turns `max` on (`/ladder:edit`), a command that launches a Claude
session at `--effort max` asks for their yes first, and runs at `xhigh` while `/handoff:away` is
set. With `max` off the gate stays silent.
