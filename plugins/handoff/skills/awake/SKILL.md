---
name: awake
description: Mark the user as out but reachable from a phone, so the computer stays awake between turns and sessions keep asking them instead of deciding unattended. Use when the user types /handoff:awake, or says they are going out but can still answer from the phone.
---

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" self` for this session's name, then
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" awake --by "<name>"` (the name in quotes;
without a name, `awake` alone), and relay its lines: the first is the flag (`awake: since
<time>`), and `away: off` follows when an away flag was set, which this clears.

Then tell the user, plainly:

- **Sessions still ask them.** Nothing is decided "(auto)": a question waits for their answer,
  which they give from the phone over Remote Control. A launch at effort `max` still asks too.
  To have the sessions decide alone instead, that is `/handoff:away`.
- **The computer is kept awake between turns** for 12 hours or until `/handoff:back`, from each
  session's next turn end, with or without Remote Control attached, and with `autoretry=on` a
  session an API error stopped carries on as it does while away. **A closed laptop lid still puts
  it to sleep**: leave the lid open and the power connected. Where `caffeinate` does not exist
  (anything but macOS), nothing is kept awake: use the system's own power settings.
- `/handoff:back` clears it, and the away flag with it.
