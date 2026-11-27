# Recipes

flowkit ships its pieces in three tiers:

| Tier | What it is | How you get it | Promise |
|---|---|---|---|
| Plugin | A versioned plugin under `plugins/`, with a setup command | `claude plugin install <name>@flowkit` | Updates arrive by version; a setup command writes and removes its own config |
| Recipe | A script plus a page in this folder | You copy it into place by hand | None: read it, adapt it, own your copy |
| Seam | A documented file or command a plugin exposes for you to build on | Nothing to install | Documented in the README of the plugin that ships it |

A recipe is for something that changes your machine outside Claude Code (a power setting, a
launchd job, a shell hook, a guard on git) where a setup command that writes it for you would be
the wrong default. You read it, decide, and install it yourself.

## What every recipe page carries

Each recipe is one folder, `recipes/<name>/`, with a `README.md` in this order:

1. **Undo**: the one line that removes it again, first, before anything else.
2. **What it does**: the problem it solves, in a few lines.
3. **The script**: the file in the folder, and every value you are expected to change.
4. **What it does not catch**: the cases it misses, said plainly, so you know where it stops.
5. **Install**: the copy-paste steps, with nothing left as a placeholder you cannot fill.

## The rules

- **Installed by hand.** No plugin installs a recipe, and no setup command writes one. The
  recipe's page is the whole installer.
- **One public copy.** The recipe in this folder is the only one flowkit keeps. If you change it
  for yourself, your copy is yours; a fix worth sharing comes back here as a pull request.
- **No version promise.** A recipe has no version and no update path. A change here does not reach
  your copy: compare it by hand when you want the newer one.
- **Generic.** A recipe names no employer, person, account or machine path; anything specific to
  one setup is a value you set, shown with an example.

No recipe has shipped yet; each arrives with the slice that builds it.
