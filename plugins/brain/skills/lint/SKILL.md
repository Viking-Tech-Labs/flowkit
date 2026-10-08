---
name: lint
description: Health-check the user's brain vault - index drift, dangling links, orphans, frontmatter, log order, runbook trust stamps, missing symptoms, stale pages. Reports only and changes nothing. Use when the user says "lint the brain", "tidy the wiki", "health check", or after a batch of saves.
---

Run a health check on the user's vault. Report findings only. **Do not auto-fix.** Ask before any change.

## 0. Find the vault

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault.py" resolve "${CLAUDE_PLUGIN_DATA}"`. It prints the vault's absolute path; call it VAULT. If it exits non-zero, relay its message and stop (the usual fix is `/brain:init`).

## 1. Run the script first

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/brain-lint.py" "VAULT"` (with VAULT filled in). It runs every check that needs no judgement and changes nothing:

- index drift, both ways (a page with no index entry, an entry with no page)
- dangling `[[links]]` (code spans and fences are skipped)
- orphans, minus the slugs in `VAULT/.brain-hubs` if that file exists
- frontmatter fields (`title`, `tags`, `created`, `updated`) and valid dates
- `log.md` in date order, every entry `- YYYY-MM-DD:` or `- YYYY-MM-DD (qualifier):`
- runbook provenance: a `**Confirmed working <date>**` stamp, "Not yet human-verified", or neither; stamps over 180 days
- runbook and troubleshooting pages missing `symptoms:` or a `## Symptoms` section
- pages not updated for 90 days

Relay its findings and its summary line as they are. Do not redo these checks by reading pages. Exit code 1 means defects; orphans, old stamps and stale pages are for a human look and leave the exit at 0.

## 2. Judge what it found

Open only the pages the script names, and decide together with the user:

- Orphans: add a backlink from somewhere, list the slug in `VAULT/.brain-hubs` if it is a deliberate entry point (one slug per line), or delete the page.
- Stale pages: still true, or worth a refresh.
- Runbooks with no trust line or an old stamp: candidates for a human re-confirm. Never re-stamp them yourself; only a human run can set or refresh a stamp.
- Misses: if `VAULT/inbox.md` has `MISS:` lines, say so and offer `/brain:inbox` to fix the index keywords they point at.

Format the report as short sections. Be terse: file paths and one-line reasons.
