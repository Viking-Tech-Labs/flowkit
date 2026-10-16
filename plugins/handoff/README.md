# handoff

Close a Claude Code session so the next one starts cold, with nothing lost and nothing worked out
twice: green work committed, stale docs fixed, a pick-up block written into the repo's plan doc,
the next session's prompt drafted when repo work is left, and the close recorded in the story's
file.

Install, after adding the flowkit marketplace (see the root README): `claude plugin install handoff@flowkit`.

Skills are always typed with the plugin's name in front (`/handoff:close`), so a skill of your own
called `handoff` keeps working beside this one.
