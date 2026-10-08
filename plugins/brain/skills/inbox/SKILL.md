---
name: inbox
description: Drain the inbox of the user's brain vault - turn each fleeting one-line capture into a runbook, a concept page, a raw dump, an index fix for a retrieval miss, or discard it. Use when the user says "drain the inbox", "process the inbox", "go through my captures", or when inbox.md has entries at the start of a vault session.
---

`inbox.md` is the low-friction front door: single lines dropped in by hand, pasted, or appended by `/brain:recall` as `MISS: <query>` when a lookup failed. It is a staging area, not a store, and nothing lives there long.

## 0. Find the vault

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault.py" resolve "${CLAUDE_PLUGIN_DATA}"`. It prints the vault's absolute path; call it VAULT. If it exits non-zero, relay its message and stop (the usual fix is `/brain:init`). Use absolute paths under VAULT; do not `cd`.

## 1. Drain it

1. Read `VAULT/inbox.md`. If it is empty below the `---`, say so and stop.
2. For each entry, decide where it belongs and act:
   - a fix goes to a `wiki/` runbook;
   - an idea, a learning or a person goes to a `wiki/` concept page, or folds into an existing page;
   - verbatim external material goes to `raw/`;
   - a `MISS: <query>` line means a lookup failed: find the page that should have matched and fix the index keyword or the page description so it does next time;
   - if it is not worth keeping, discard it.
3. Remove each processed line from `inbox.md` as you go. Do not batch: process now, while the context is fresh.
4. Anything promoted to a page follows the save protocol in full (search first, page format, backlinks, index entry, log line, hot summary); that protocol is the `brain:save` skill.
5. Commit once at the end if the vault is a git repository: `git -C VAULT add -A && git -C VAULT commit -m "Drain inbox: <n> entries"`. One line, present tense, no emoji. Never push.

Keep the bar: not every fleeting line becomes a page. A page needs enough to say. Discarding is fine and expected.

Finish with one line: how many entries were promoted, folded in, fixed as misses, and discarded.
