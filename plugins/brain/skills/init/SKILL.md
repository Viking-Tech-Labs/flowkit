---
name: init
description: Create the user's brain vault at a path, or register one that already exists, so the other brain skills find it. Run once per machine. Builds the vault from the template inside the plugin, starts git there, and writes the vault's settings file that registers the flowkit marketplace.
argument-hint: [vault-path]
disable-model-invocation: true
---

Create or register the user's vault so `/brain:save`, `/brain:recall`, `/brain:lint` and `/brain:inbox` can find it from any directory.

1. Pick the path: `$ARGUMENTS` if the user gave one, otherwise the current directory.
2. Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/vault.py" init "${CLAUDE_PLUGIN_DATA}" "${CLAUDE_PLUGIN_ROOT}" "<path>"` (with the path filled in). Do not create or copy any vault file by hand; the script does all of it:
   - A path that does not exist, or an empty directory, becomes a new vault copied from the template that ships in this plugin, with git started on `main` and a first commit.
   - A path that is already a vault (it holds `wiki/` and `index.md`) is only registered; nothing in it is overwritten.
   - Any other non-empty directory is refused with exit code 1. Relay the message and stop; never empty or overwrite the directory.
   - Either way it writes `<path>/.claude/settings.json` if the vault has none (it enables the `brain` plugin for sessions opened in the vault and registers the marketplace it comes from) and saves the absolute path to a file in the plugin's data directory. That file is per machine and survives plugin updates.
3. Relay what the script printed, then tell the user, in three lines at most: the vault path now saved; that the environment variable `BRAIN_DIR` overrides it if set; and that `_me.md` in the vault is the one file to fill in by hand, because every vault session reads it first.

If the user asks to move or change the vault later, run this skill again with the new path.
