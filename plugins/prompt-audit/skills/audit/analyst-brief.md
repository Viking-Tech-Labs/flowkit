# Prompt-audit analyst brief

You are analysing prompts one person typed (or dictated, or pasted) into Claude Code sessions.
The goal is to find what they keep asking for that nothing encodes, and what they keep asking
for although something already encodes it. Every repeat is a turn they spent that a rule, a
skill, a hook or a script could have spent for them.

The prompts are private. Write nothing to disk outside the work directory you were given,
and quote only in your reply.

## Read, in this order

1. The prompt files you were given: one prompt per paragraph, headed
   `[timestamp session-prefix branch]`. Read every prompt in full. They may be dictated: read
   phonetic errors charitably.
2. What is already encoded: the instruction files you were given (the user's own `CLAUDE.md`,
   the target file, each project's `CLAUDE.md`), including any managed block between
   `<!-- flowkit:<name> begin ... -->` and `<!-- flowkit:<name> end -->`, and the names and
   descriptions of the skills listed in your session.
3. The last audit's report, when you were given one: its patterns table.

Do not read the transcripts themselves or the projects' code. You may open one transcript
(find it under the transcripts directory by the session prefix) only to check what a terse
prompt was replying to, when the pattern depends on it.

## Classify every repeat

A pattern is something the user asked for in at least three prompts, or in two prompts in
different sessions when the wording shows frustration ("again", "I told you", "why did you
stop", "as always").

- **NEW**: nothing encodes it.
- **LEAK**: a rule, skill or hook already covers it, and they still had to ask. Say which
  rule and where it is, and your best read on why it leaked: the rule is worded too narrowly,
  it is in a file those sessions never loaded, it competes with another instruction, or the
  model ignored it.
- **SHIFT**: a pattern in the last report's table that has gone quiet or dropped sharply.
  Report both counts, so the last round's rules can be judged. A pattern that has not dropped
  is a LEAK. Without a last report there are no SHIFT rows.

Ignore one-off task content (the feature being built). Count the asks about *how the work is
done*: starting, continuing and stopping; checking back and progress reports; version control
and review ritual; tests and verification; the shape and length of answers; decisions and
questions; handing work over; cost and model choice; secrets; notes and documentation.

## Report back

Keep it short. A table, most prompts first, at most 15 rows:

| # | Pattern | Class | Prompts | Sessions | Quotes (≤3, verbatim, ≤120 chars each) | Covered by / leaked from | Proposal |

"Proposal" is one of: a rule (write the one or two sentences you would add, as an instruction
to a future session), a rule for one project's file (which), a follow-up a rule cannot carry
(a hook, a script, a skill change: one line on what it would do), or "nothing: judgment call".
Then at most five lines of anything else worth knowing: patterns under the threshold that look
like they are starting, and rules that seem to be working.

Never quote a secret. If a prompt holds a credential, report the timestamp and session prefix
only.
