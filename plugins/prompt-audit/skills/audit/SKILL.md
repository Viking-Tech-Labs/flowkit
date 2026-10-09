---
name: audit
description: Read the prompts the user typed into Claude Code since the last audit, find what they keep asking for by hand (NEW - no rule encodes it, LEAK - a rule covers it and sessions still miss it, SHIFT - what went quiet since the last audit), interview them on the fixes and write the accepted rules into their own section of CLAUDE.md. Use when the user types /prompt-audit:audit.
argument-hint: [since YYYY-MM-DD] [transcripts <directory>] [into <CLAUDE.md or its directory>]
disable-model-invocation: true
---

Every prompt the user repeats is a turn that a rule could have spent for them. Find those repeats in their own typed prompts, propose a rule for each, get their decision on each, and write the accepted ones. Scripts do the extract, the bookkeeping and every write to a CLAUDE.md; never edit that file by hand here.

Arguments: `$ARGUMENTS`

Read them as three optional parts, in any order: `since <date>` (the window's start; without it the window starts where the last audit ended, and a first audit reads the whole history), `transcripts <directory>` (a `projects/` directory to read instead of the one in the Claude configuration directory), and `into <path>` (the CLAUDE.md the rules go to, or its directory; without it the user's own `CLAUDE.md` in their Claude configuration directory, `~/.claude`, because habits should hold in every repository). Leave out of the commands whatever the user did not give.

## The prompts are private

The extract holds the user's prompts word for word. It goes into a work directory outside any git repository, called `<work>` below: `prompt-audit` in this session's scratch directory when it has one, else `${CLAUDE_PLUGIN_DATA}/work`. Never the project's directory; the script refuses a directory inside a repository. Write no file derived from the prompts anywhere else except the report, at the place step 7 names. Commit nothing. Quote a prompt only in the analysis and the report, at most 120 characters, and never a credential.

## 1. Extract

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/extract.py" run "${CLAUDE_PLUGIN_DATA}" <work> [--since <date>] [--transcripts <directory>]`

It prints the window and where it came from, the count per kind and per project, the last audit's report and the brain vault when there is one, and it writes one `<project>.txt` per project (typed prompts only, each headed `[timestamp session-prefix branch]`), `projects.tsv` (project, typed prompts, working directory) and `prompts.jsonl` (every user turn with its kind). Only `typed` is the corpus. `command` and `accepted` count as evidence of what the user reaches for; `launch` is machine-drafted and is left out.

- No typed prompts in the window: say so and stop. Record nothing.
- If prompts that a script or another session drafted show up among the typed ones (the same long template again and again), tell the user, and with their yes add one regular expression per line to `${CLAUDE_PLUGIN_DATA}/launch-patterns.txt`, then run the extract again.
- `possible secrets` lists where a credential may sit (time, project, session), never its value. Tell the user which sessions, and that a credential typed into a prompt should be rotated: deleting the line removes the copy, not its validity. Never print one.

## 2. What is already encoded

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/rules.py" list [path]`

It prints the target file, the managed blocks in it and the rules earlier audits wrote. Then read the target file itself, the user's own `CLAUDE.md` when the target is another file, and for each project in `projects.tsv` the `CLAUDE.md` and `.claude/CLAUDE.md` in its working directory when they exist. A managed block (the text between `<!-- flowkit:<name> begin ... -->` and `<!-- flowkit:<name> end -->`) is read like any other rules: an ask it covers that the user still had to make is a LEAK. The skills listed in this session count as encoded too.

If the extract named a last report, read its patterns table: it is what SHIFT is judged against, and its undecided proposals come back into this round's list. With no last report there is no SHIFT this round.

## 3. Analyse

The method is `${CLAUDE_PLUGIN_ROOT}/skills/audit/analyst-brief.md`: what counts as a pattern, the three classes, the table to produce.

- Up to about 300 typed prompts: read the brief and every project file yourself.
- More: split the project files into slices of roughly equal prompt count, keeping projects that belong together in one slice, and give each slice to a subagent in one message, on this session's model. Its prompt: follow the brief at that path, then the slice's files, the instruction files from step 2 and the last report's path. Do not read the slices yourself. When subagents are not available, read the slices one after another.

## 4. Merge and propose

The same ask in two projects is one pattern with the counts added. Rank by prompts saved. For each pattern, decide what you propose before asking anything:

- **A rule**, the default: one or two sentences, written as an instruction to a future session, specific enough to check. This is the only thing the audit writes.
- **A rule in a project's file** when the ask is one repository's fact. It is written only when the user names that file.
- **Something a rule cannot do**: a step that is mechanical and keeps leaking (a hook or a script), or one a skill should already take. Describe it as a follow-up in the report. The audit does not build it.
- **Nothing** when it is a judgment call or under the threshold.

A LEAK gets a diagnosis before a fix, because a rule that exists and still leaks is rarely cured by saying it louder. Say which cause holds: worded too narrowly, in a file those sessions never load, competing with another instruction, or ignored. Then propose for that cause. When the leaking rule sits inside a managed block, never propose editing the block: propose a sharper rule of the user's own beside it, and say that they may delete the block's rule themselves.

Before proposing a rule that changes how every session behaves, try to break it: a session type it would hurt (unattended, a subagent, a review), a rule already in the file it contradicts. Carry what survives with the proposal.

## 5. Interview

Present the whole list first, numbered, most prompts first. Each line: the pattern, its class, prompts and sessions, one quote, and the proposal with its exact wording, recommended option first. SHIFT rows need no decision; they are evidence that a rule worked.

Then get a decision on each: accept, reword, or drop. If the `session-discipline` plugin's decision-interview skill is available, follow it. Otherwise: ask with the AskUserQuestion tool in themed batches of at most four, the recommended option first with a line of why and the alternative with its trade-off; without that tool, the same as a numbered list with lettered options. Read a custom answer or a question back as what it is, not as a pick. When the user goes against a recommendation, confirm once with the consequence in plain words.

When nobody can answer (a headless or scheduled run, or the question tool is not there and no reply will come): print the numbered list, write nothing to any CLAUDE.md, mark every proposal undecided in the report, and go on to step 7. The next audit brings them back.

## 6. Land

Write the accepted rules, with the wording the user agreed to, as a JSON list in `<work>/rules.json`, `[{"id": "<two-to-four-lowercase-words-joined-by-dashes>", "text": "<the rule, one line>"}]`, then

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/rules.py" add <work>/rules.json [path]`

It writes each rule as a bullet under the heading `## Rules from prompt audits`, which it creates at the end of the file when missing, and reports each rule as added, unchanged or kept. It never writes inside a managed block. `kept` means a rule with that id is already there with another text: show both to the user and run again with `--replace` only on their yes. Exit code 2 means a managed block's markers are damaged and nothing was written: relay the message with its line numbers and stop; do not repair the file yourself.

Tell the user that the section is theirs to edit, reorder or cut, and that the rules take effect in the next session.

## 7. Report and record

Write the report to `<work>/report.md`: the window and corpus numbers; a table `| Pattern | Class | Prompts | Sessions | Quote | Decision |` with the decision as accepted (and the rule's id), dropped, undecided or no decision needed; the follow-ups a rule cannot carry; the possible-secret locations. Then

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/extract.py" record "${CLAUDE_PLUGIN_DATA}" <work> <work>/report.md`

which notes the window as audited and keeps the report in the plugin's data directory, where the next audit finds it. Run it only after the report file is written, and whether or not anything was accepted; run again with a corrected report, it replaces the one kept for that window.

The data directory is the report's home. Copy it elsewhere only when the user asks: to a path they name, or, when the extract printed a vault, as a page there if they say yes. Before writing it into a git repository, a vault included, say that the report quotes their prompts and that the repository's history keeps them, and offer the version without the Quote column.

Close with what was found (counts per class), what was written and where, what is undecided, and the follow-ups that need the user's hands.
