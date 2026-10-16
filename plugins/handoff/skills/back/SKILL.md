---
name: back
description: Clear the away flag, so sessions ask the user again instead of deciding unattended. Use when the user types /handoff:back, or says they are back.
---

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" back` and relay its line. Then say that
whatever was decided while they were away is marked "(auto)" in the closes recorded under
`~/.claude/stories/` and in the repos' pick-up blocks, for them to review.
