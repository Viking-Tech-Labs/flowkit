---
title: <name of the fix>
tags: [work, runbook, troubleshooting]
symptoms: [<error keywords - e.g., 504, gateway timeout, ECONNREFUSED>]
created: YYYY-MM-DD
updated: YYYY-MM-DD
---

One-line description of what's broken and what the fix is. Link to [[related-page]] where relevant.

## Symptoms (when this is the answer)

- <observable symptom 1>
- <observable symptom 2>
- <observable symptom 3>

## Fix

```sh
<commands>
```

## Verify

```sh
<command that confirms the fix worked>
```

Expected output: <what success looks like>

**Confirmed working <YYYY-MM-DD>** (on <host / tool version>)
<!-- Provenance signal: set/refresh this line ONLY when the fix has actually been run and seen to
     work by a human, not when Claude merely wrote or restated it. Re-stamp the date (and host/
     version if changed) each time it's re-confirmed. This is what separates a human-verified fix
     from an AI-asserted one; git history can't. If never human-run yet, write "Not yet human-verified". -->

## Why

_(optional: only if the reason isn't obvious. Skip otherwise.)_

<!-- If this fix ever changes/replaces an older page, retire the old one: add
     "> Superseded <YYYY-MM-DD> by [[new-slug]]" at its top instead of leaving two competing answers. -->
