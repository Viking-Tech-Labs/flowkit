# prompt-audit

Nothing in this plugin yet; version 0.1.0 reserves the name. Build slice S3 puts here a skill that
reads the prompts you typed into Claude Code since the last audit, finds what you keep asking
for by hand (asks no rule encodes yet, and asks a rule already covers that sessions still miss),
interviews you on the fixes, and lands the accepted ones in your own section of `CLAUDE.md`,
never in the managed block of `session-discipline`. The window of the last audit is kept in the
plugin's data directory, so it survives plugin updates, and no path on the author's machine is
baked in.

Install, after adding the flowkit marketplace (see the root README): `claude plugin install prompt-audit@flowkit`.
