---
name: back
description: Clear the away flag, so sessions ask the user again instead of deciding unattended, and show which sessions wait on them now. Use when the user types /handoff:back, or says they are back.
---

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" self` for this session's name, then
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" back --by "<name>"` (the name in quotes;
without a name, `back` alone), and relay its lines: the flag is off, then one line per session waiting on the user right
now (or `nobody waits on you`), so they know where to go first. Every session hears at its next
turn that the flag was cleared, by whom and when. Then say that whatever was decided while they
were away is marked "(auto)" in the closes recorded under `~/.claude/stories/` and in the repos'
pick-up blocks, for them to review.
