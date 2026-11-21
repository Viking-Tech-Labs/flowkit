---
name: away
description: Mark the user as away, so sessions and closes run unattended (no questions, the recommended option at every fork, marked "(auto)"), reach the sessions already parked on a question, and keep the computer awake between turns. Use when the user types /handoff:away, or says they are going to bed, going out or will be off for hours.
---

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" self` for this session's name, then
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" away --by "<name>"` (the name in quotes: a
session started by hand carries its title, spaces and all; without a name, `away` alone, and the
flag then says `unknown`), and relay its lines: the first is the flag (`away: from
<time>`, when it takes effect), the second how much of the plan's usage is left, the rest the last
call: one line per coordinator (`last call <name>`, or `no last call <name>: <why>`: busy,
waiting, a draft in its prompt box, not in a tmux pane). A flag already set stays as it is, and
the first line says who set it.

Then tell the user, plainly and in this order:

- **Five minutes of last call.** The flag takes effect at the time on its first line. Until then
  questions still go through: every idle coordinator was just told the user is leaving and to ask
  now what it needs, so answer what comes up before going.
- **Then no session asks them anything:** the plugin's hook denies every question while the flag
  is in effect, and the session takes the recommended option, marked "(auto)" for them to review.
  A session they typed to (or whose question they answered) in the last five minutes may still
  ask, so a conversation in progress is not cut off. At that time the sessions already sitting on
  a question are told the same, once (the tmux status bar's tick runs it); one waiting on a
  permission prompt, or on a question outside tmux, still waits for them (answer it from the
  phone), since nothing here approves on their behalf. Every session hears of the flag, who set it
  and when, at its next turn.
- The flag clears with `/handoff:back` or by itself 12 hours after it took effect.
- The computer is kept awake between turns while the flag is set, but **a closed laptop lid
  still puts it to sleep and pauses the chain** until it is opened again. Leave the lid open and
  the power connected. Where `caffeinate` does not exist (anything but macOS), nothing is kept
  awake: use the system's own power settings.
