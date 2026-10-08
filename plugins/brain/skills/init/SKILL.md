---
name: init
description: Point the brain skills at the user's vault. Run once per machine, from inside the vault directory or with its path. Writes the vault path where the other brain skills find it.
argument-hint: [vault-path]
disable-model-invocation: true
---

Register the user's vault so `/brain:save`, `/brain:recall`, `/brain:lint` and `/brain:inbox` can find it from any directory.

1. Pick the path: `$ARGUMENTS` if the user gave one, otherwise the current directory.
2. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault.py" check "<path>"` (with the path filled in).
   - If it exits non-zero, the directory is not a vault yet (a vault holds `wiki/` and `index.md`). Do not create the skeleton by hand. Tell the user to copy the kit's `template/` directory to where the vault should live (the README of the flowkit marketplace has the command), run `git init -b main` there if they want history, then run `/brain:init` again from inside it. Stop.
   - If it exits 0 but lists missing parts (`raw`, `log.md`, `hot.md`, `inbox.md`, `_me.md`, `templates`), carry on and mention them: the skills work without them but the template has all of them.
3. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault.py" set "${CLAUDE_PLUGIN_DATA}" "<path>"`. It writes the absolute path to a file in the plugin's data directory. That file is per machine and survives plugin updates.
4. Tell the user, in three lines at most: the vault path now saved; that the environment variable `BRAIN_DIR` overrides it if set; and that `_me.md` in the vault is the one file to fill in by hand, because every vault session reads it first.

If the user asks to move or change the vault later, run this skill again with the new path.
