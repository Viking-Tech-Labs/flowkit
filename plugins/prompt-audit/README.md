# prompt-audit

Reads the prompts you typed into Claude Code since the last audit and finds what you keep asking
for by hand, then turns the repeats you choose into rules in your own `CLAUDE.md`.

Install, after adding the flowkit marketplace (see the root README): `claude plugin install prompt-audit@flowkit`.

| Skill | What it does |
|---|---|
| `/prompt-audit:audit` | Audits the window since the last audit; the first audit reads the whole history Claude Code still keeps. |
| `/prompt-audit:audit since 2026-01-31` | The same from a date you give. |
| `/prompt-audit:audit transcripts <directory>` | Reads another `projects/` directory, for example one you archived. |
| `/prompt-audit:audit into <path>` | Writes the accepted rules to that `CLAUDE.md` (or the one in that directory) instead of your own. |

It runs only when you type it. No hook, nothing at session start.

## What it finds

- **NEW**: something you asked for in three or more prompts that no rule, skill or hook encodes.
- **LEAK**: something a rule already covers that you still had to ask for, with a read on why the
  rule did not hold (too narrow, in a file those sessions never load, contradicted, ignored).
- **SHIFT**: a pattern from the last audit that went quiet, which is the evidence that a rule
  worked. From the second audit on.

You get the whole list first, numbered, each with a proposed rule in its exact wording. Then an
interview: accept, reword or drop, one decision per pattern. If the `session-discipline` plugin is
installed its decision interview is used; without it the audit asks the same way on its own. In a
run nobody can answer (headless, scheduled) it prints the list, writes no rule and brings the
proposals back next time.

## Where things go

- **Rules** go under the heading `## Rules from prompt audits` in your own `CLAUDE.md`
  (`~/.claude/CLAUDE.md`, or the one in `CLAUDE_CONFIG_DIR`), one bullet each, ending in a small
  comment with the rule's id. That id is how a second run knows the rule is already there. The
  section is yours: edit, reorder or delete rules as you like. Nothing is ever written between the
  markers of a managed block such as the one `session-discipline` writes; a rule that leaks from
  inside a block gets a sharper rule of your own beside it.
- **State** lives in the plugin's data directory, which survives plugin updates: `runs.json` (the
  windows audited so far), `reports/` (each audit's report, which the next one compares against)
  and, if you write one, `launch-patterns.txt`.
- **Your prompts stay private.** The extract is written to the session's scratch directory, or to
  `work/` in the plugin's data directory, never into a git repository (the script refuses one),
  and nothing from it is committed anywhere. Each run replaces the previous run's extract. The
  report quotes prompts in at most 120 characters; it is copied out of the data directory only
  when you ask, to a path you name or, with the `brain` plugin's vault, as a page there, and you
  are warned before it lands in a repository. A possible credential in a prompt is reported by
  session and time, never by value.
- **A resumed session counts once.** Claude Code resumes a session under a new id with a copy of
  its history and leaves a `continued-in` marker in the old transcript. The extract puts the
  prompts under the last id of the chain and drops the copies; the summary says how many
  (`continued sessions: N (M prompts merged), fork copies dropped: K`, the forks being copies that
  share a prompt without a marker).

## Machine-drafted prompts

A prompt that a script or another session wrote for you looks typed in a transcript and would
drown the corpus. The extract already sets aside the long first prompt of a session that was named
before its first turn. For your own formats, put one regular expression per line in
`launch-patterns.txt` in the data directory; the audit offers to do that when it sees the same
template repeating.

## The scripts on their own

Python 3, standard library only.

```
python3 scripts/extract.py run <state-dir> <scratch-dir> [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--transcripts DIR]
python3 scripts/extract.py record <state-dir> <scratch-dir> [report.md]
python3 scripts/extract.py runs <state-dir>
python3 scripts/rules.py list [path]
python3 scripts/rules.py add <rules.json> [path] [--replace]
```
