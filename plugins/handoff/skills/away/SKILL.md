---
name: away
description: Mark the user as away, so sessions and closes run unattended (no questions, the recommended option at every fork, marked "(auto)") and the computer is kept awake between turns. Use when the user types /handoff:away, or says they are going to bed, going out or will be off for hours.
---

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" away` and relay its line.

Then tell the user, plainly and in this order:

- Until they come back, every close and every session that reads the flag runs unattended: no
  questions, the recommended option at each fork, marked "(auto)" for them to review.
- The flag clears with `/handoff:back` or by itself after 12 hours.
- The computer is kept awake between turns while the flag is set, but **a closed laptop lid
  still puts it to sleep and pauses the chain** until it is opened again. Leave the lid open and
  the power connected. Where `caffeinate` does not exist (anything but macOS), nothing is kept
  awake: use the system's own power settings.
