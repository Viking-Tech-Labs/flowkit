---
name: recall
description: Recall notes from the user's brain vault by topic or symptom, going index to page to links rather than searching the whole vault. Use when the user says "check the brain", "what does the brain say about X", "what was that thing about X", "remind me about Y", "I don't remember how X works", or describes a symptom or error ("I'm getting a 504", "X is broken") that a saved runbook may answer.
argument-hint: <topic or symptom>
---

The user is asking about:

$ARGUMENTS

If that is empty, the question is the user's last message.

## 0. Find the vault

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault.py" resolve "${CLAUDE_PLUGIN_DATA}"`. It prints the vault's absolute path; call it VAULT. If it exits non-zero, relay its message and stop (the usual fix is `/brain:init`). Use absolute paths under VAULT; do not `cd`.

## 1. Look up, in this order

1. Read `VAULT/hot.md`. It is cheap, and recent context may already have the answer.
2. Read `VAULT/index.md` and scan for:
   - matching topic names, and
   - matching symptom keywords: runbook entries carry them in **bold** (error text, status codes, "not responding").
3. Open the most likely one to three candidate pages under `VAULT/wiki/<slug>.md`. For a symptom query, check each page's `symptoms:` frontmatter and its `## Symptoms` section. When several pages match, prefer the one whose symptom list is the most specific. The page that solves a symptom is named after the fix, not the symptom.
4. Follow `[[backlinks]]` only if the answer is not on the first page.

Do not grep or read the whole vault: index to page to links is the protocol. Leave `VAULT/reference/`, if the vault has one, alone unless the user names it.

## 2. Answer

Answer concisely. Show commands and values directly, no preamble. If multiple pages could apply, list them with one-line descriptions and ask which to expand.

When the answer is a runbook, say what its `## Verify` section says about trust: the `**Confirmed working <date>**` stamp with its date and environment, or "Not yet human-verified". Point out a stamp older than 180 days. Stamps are never set or refreshed by recalling; only `/brain:save` does that, and only after a human run.

## 3. Record a miss

If nothing matched, or the page you surfaced turned out to be the wrong one (the user says so, or you had to grep as a fallback), append a line `MISS: <the query>` to `VAULT/inbox.md`. That records a retrieval failure so the next `/brain:lint` or `/brain:inbox` can add the missing index keyword or page description. The vault learns from misses instead of silently repeating them.
