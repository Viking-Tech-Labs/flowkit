# brain

A personal knowledge wiki in plain markdown, driven by five skills. The vault is a directory
you own (a copy of the kit's `template/`); this plugin carries the protocol for working in it.

Install, after adding the flowkit marketplace (see the root README): `claude plugin install brain@flowkit`.

| Skill | What it does |
|---|---|
| `/brain:init [path]` | Registers your vault once per machine. |
| `/brain:save <thing>` | Saves a fix, concept, person or raw dump: searches first, writes the page in the vault's format, updates `index.md`, `log.md`, `hot.md`, commits. |
| `/brain:recall <query>` | Looks up by topic or symptom: `hot.md`, then `index.md`, then the one to three best pages. Records misses in `inbox.md`. |
| `/brain:lint` | Runs `brain-lint.py` (index drift, dangling links, orphans, frontmatter, log order, runbook trust stamps, stale pages) and reports; fixes nothing. |
| `/brain:inbox` | Drains `inbox.md` into pages, index fixes or the bin. |

`save`, `recall`, `lint` and `inbox` also fire from plain language ("save this", "what does the
brain say about X", "drain the inbox"). `init` runs only when you type it.

## Where the vault is found

In this order: the `BRAIN_DIR` environment variable; the path file `/brain:init` writes into the
plugin's data directory (kept across plugin updates); the current directory, when it holds
`wiki/` and `index.md`. A directory is a vault when it has `wiki/` and `index.md`.

## What it keeps from the protocol

- Symptom-first retrieval: runbooks carry `symptoms:`, a `## Symptoms` section and their keywords
  in bold in the index entry.
- The `**Confirmed working <date>**` stamp, set only after a human run; otherwise "Not yet
  human-verified".
- Supersession: a replaced page gets `> Superseded <date> by [[new-slug]]`, never two answers.
- Search before write, flat `wiki/`, raw files never edited, plain markdown only.

`scripts/brain-lint.py` is also usable on its own: `python3 brain-lint.py <vault>`. List the slugs of
deliberate stand-alone pages, one per line, in `<vault>/.brain-hubs` to keep them out of the
orphan list.
