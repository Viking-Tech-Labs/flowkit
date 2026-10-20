---
name: away
description: Mark the user as away, so sessions and closes run unattended (no questions, the recommended option at every fork, marked "(auto)"), reach the sessions already parked on a question, and keep the computer awake between turns. Use when the user types /handoff:away, or says they are going to bed, going out or will be off for hours.
---

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" away` and relay its lines: the first is
the flag, the second how much of the plan's usage is left, the rest are the sessions that were
waiting on the user (`nudged`, `permission`,
`unreachable`, `skipped`, or `no session waits on you`).

Then tell the user, plainly and in this order:

- Until they come back, no session asks them anything: the plugin's hook denies every question
  while the flag is set, and the session takes the recommended option, marked "(auto)" for them
  to review. A session that was already sitting on a question has been told the same (`nudged`);
  one waiting on a permission prompt, or on a question outside tmux, still waits for them (answer
  it from the phone), since nothing here approves on their behalf.
- The flag clears with `/handoff:back` or by itself after 12 hours.
- The computer is kept awake between turns while the flag is set, but **a closed laptop lid
  still puts it to sleep and pauses the chain** until it is opened again. Leave the lid open and
  the power connected. Where `caffeinate` does not exist (anything but macOS), nothing is kept
  awake: use the system's own power settings.
