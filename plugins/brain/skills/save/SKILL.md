---
name: save
description: Save a fix, concept, person or note to the user's brain vault following the vault protocol - a symptom-indexed runbook, an atomic concept page, or a raw dump - then update the index, log and hot summary. Use when the user says "save this", "add to brain", "wiki this", "remember this fix", or hands over a note to keep.
argument-hint: <what to save>
---

Save the following to the user's brain vault:

$ARGUMENTS

If that is empty, save what the conversation has just settled. Ask one question only when the title or the kind of page is genuinely ambiguous; otherwise just do it.

## 0. Find the vault

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault.py" resolve "${CLAUDE_PLUGIN_DATA}"`. It prints the vault's absolute path; call it VAULT. If it exits non-zero, relay its message and stop (the usual fix is `/brain:init`). Use absolute paths under VAULT for everything below; do not `cd`.

Read `VAULT/_me.md` first if you have not this session, so you do not ask what it already answers.

## 1. Search before you write

Never propose a new page, and never ask "new page or fold into an existing one?", until you have actually looked:

- `grep -i "<topic>" VAULT/index.md` for an entry that already covers it, and
- `grep -rn -iE "<distinctive keyword>|<command>|<error string>" VAULT/wiki/` for the mechanism itself. The page that covers it is often named after a different symptom and will not match the topic word.

Then act on the result. An existing page covering the same mechanism is extended, not duplicated. A page whose answer this one replaces is retired (step 3). Only genuinely new ground gets a new file. When you do offer the user the new-page-or-fold-in choice, state what the search found: the candidates you considered and why they do not fit.

## 2. Choose the surface

- A raw dump (an article, a transcript, a long quote, anything verbatim) goes to `VAULT/raw/YYYY-MM-DD-slug.md` with frontmatter `title`, `source`, `created`. Raw files are never edited afterwards. Then summarize what matters into a wiki page.
- Anything synthesized (a fix, an idea, a learning, a fact, a person, a project, a decision) goes to `VAULT/wiki/slug.md`. One concept per file, flat: no folders inside `wiki/`.

Start from the vault's template, copy and fill in: `VAULT/templates/runbook.md` for fixes and troubleshooting, `concept.md` for ideas, tools and decisions, `person.md` for people. If a template is missing, follow the page format below.

## 3. Page format

Every wiki page starts with frontmatter:

```
---
title: Human-readable title
tags: [work, runbook, ...]
created: YYYY-MM-DD
updated: YYYY-MM-DD
---
```

Tags: one scope tag, `work` or `personal` (both allowed); one kind tag from `runbook`, `troubleshooting`, `concept`, `tool`, `person`, `project`, `decision`; then free-form domain and platform tags (for example `infra`, `frontend`, `backend`, `macos`, `linux`, `web`, `mobile`). The index groups by tag, so consistency matters more than completeness.

The body is prose, not bullets by default; bullets for real lists. Link other pages with `[[slug]]`, including pages that do not exist yet (a dangling link is a signal that a page is worth writing). Plain markdown only: no callouts, no query-plugin syntax, no transclusion.

A fix or runbook also carries:

- a `symptoms:` frontmatter array of the error keywords a person would search for, and a `## Symptoms` section listing what is observable. The page is named after the fix, not the symptom, so these keywords are what make symptom-first retrieval work.
- a `## Verify` section with a `**Confirmed working <YYYY-MM-DD>** (on <host / tool version>)` line. Set or refresh it only when the fix was actually run and seen to work by the user this session. When you merely wrote or restated it, write "Not yet human-verified" instead. This line is the only thing separating a human-verified fix from an assistant-asserted one.
- supersession hygiene: when this fix replaces an older page, do not leave two competing answers. Add `> Superseded <YYYY-MM-DD> by [[new-slug]]` (a plain blockquote) at the top of the old page and point its index entry at the new one.

## 4. Update the bookkeeping

1. Add backlinks `[[other-page]]` wherever the new page references something else in the vault.
2. `VAULT/index.md`: add the entry under the right tag group as `- [[slug]] - one-line description`. For a runbook, mirror its symptom keywords into the line in **bold**.
3. `VAULT/log.md`: append one line at the bottom, `- YYYY-MM-DD: <what changed> ([[slug]])`. The log is append-only and stays date-sorted, oldest first.
4. `VAULT/hot.md`: overwrite with a short summary (about 500 characters) of what was just saved.

## 5. Commit

If `git -C VAULT rev-parse --is-inside-work-tree` succeeds, commit: `git -C VAULT add -A && git -C VAULT commit -m "Add <slug>: <one-line summary>"`. One line, present tense, no emoji. Never push. If the vault is not a git repository, skip this step and say so once.

Finish by telling the user which page was written or extended, in one or two lines.
