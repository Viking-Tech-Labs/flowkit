# session-discipline

Nothing in this plugin yet; version 0.1.0 reserves the name. Build slice S2 puts here a set of
session conventions that you adopt into your own `CLAUDE.md` as a managed block between markers:
how a session closes (done, needs-your-hands, open, left), minimal diffs, committing on green
without pushing unasked, reviews checked against acceptance criteria, copy-paste runbook answers
that were verified, secrets never printed, subagents for exploration. An `/adopt` skill shows
what is installed against what is available, writes the block, and rewrites it on `/adopt
update`, never silently and never twice. A decision-interview skill ships with it. The block
names no issue tracker, code host or terminal multiplexer.

Install, after adding the flowkit marketplace (see the root README): `claude plugin install session-discipline@flowkit`.
