# My brain

A personal knowledge wiki in plain markdown. The protocol (how to save, look up, lint and drain)
lives in the `brain` plugin's skills from the flowkit marketplace; this file only says what is
here and when each skill applies. If the skills are missing: `claude plugin install brain@flowkit`.

**First thing every session: read `_me.md`.** It says who I am, what I work on and how I like
things done. Don't ask questions it already answers. Then glance at `inbox.md`; if it has entries,
offer `/brain:inbox`.

## Layout

- `inbox.md`: fleeting one-line captures. Drained often; nothing lives here long.
- `raw/`: verbatim source material (articles, transcripts, long quotes). Never edited.
- `wiki/`: organized, atomic knowledge, one concept per file, flat. This is what gets queried.
- `templates/`: starting shapes for a runbook, a concept and a person.
- `index.md`: every wiki page, grouped by tag, runbook symptoms in bold. `log.md`: append-only
  history, newest at the bottom. `hot.md`: a short summary of what was touched most recently.
- `reference/` (optional): long-form material kept for occasional consultation; opened only when
  I name it.

## Which skill when

| I say, or this happens | Skill |
|---|---|
| "save this", "add to brain", "wiki this", I hand over a note or a fix | `/brain:save` |
| "check the brain", "what was that thing about X", "I don't remember how X works", or I describe a symptom or an error | `/brain:recall` |
| "lint the brain", "tidy the wiki", after a batch of saves | `/brain:lint` |
| "drain the inbox", `inbox.md` has entries | `/brain:inbox` |
| the skills say no vault is configured | `/brain:init`, run once from this directory |

## Always

- Look things up through `index.md`, then the page, then its links. Never read the whole vault.
- Search the vault before writing; extend a page before adding a new one.
- Keep `wiki/` flat. Never rewrite `raw/`. Plain markdown only.
- Set a runbook's `**Confirmed working <date>**` line only after I have run the fix and seen it
  work. Otherwise it says "Not yet human-verified".
- Commit changes to this vault with a one-line present-tense message; never push unless I say so.
