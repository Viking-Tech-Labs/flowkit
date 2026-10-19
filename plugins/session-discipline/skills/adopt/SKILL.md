---
name: adopt
description: Write the flowkit session conventions into the user's own CLAUDE.md as one managed block between marker comments, report what is installed against what is available, or update the block. Use when the user types /session-discipline:adopt, or says "adopt the session conventions", "is my conventions block up to date", "update the conventions block".
argument-hint: "[status | update] [path to a CLAUDE.md or its directory]"
disable-model-invocation: true
---

Manage the session-conventions block in the user's CLAUDE.md. A script does every read and write; never edit the block, the markers or the file by hand, and never write anything the user did not ask for with this command.

Arguments: `$ARGUMENTS`

Read them as an optional action (`status` or `update`; none means install) and an optional target. The target is a CLAUDE.md or a directory holding one. With no target the script uses the user's own file, `CLAUDE.md` in their Claude configuration directory (`~/.claude`), because the conventions are personal habits that should hold in every repository; a project's `CLAUDE.md` is chosen only by naming it. Leave the target out of the command when the user gave none.

The script is called as

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/adopt.py" <command> "${CLAUDE_PLUGIN_ROOT}" "${CLAUDE_PLUGIN_DATA}" [target]`

## No action: install

1. Run the `status` command and relay it: which file, the state of the block, and the flowkit plugins installed and available (one line each).
2. If the file has no block, run `install`. It appends the block at the end of the file, creating the file when it is missing, and changes nothing else. If the block is already there, `install` writes nothing: say so, and if the status showed that it differs from the plugin's, say that `/session-discipline:adopt update` would bring it up to date. Never run `update` from here.
3. Tell the user where the block is, that each rule says what it replaces so they can delete the ones they do not want, that their own rules belong outside the markers, and that the block takes effect in the next session.

## `status`

Run `status` and relay its output as it is. It writes nothing; neither do you.

## `update`

1. Run `diff`. It prints the version in the file against the plugin's and the lines that would change, and writes nothing. If it reports nothing to update, say so and stop.
2. Show the user what would change, in plain words plus the changed lines.
3. Run `update`. If it exits with code 3, the block was edited by hand since it was written (or there is no record of what was written) and nothing was changed: tell the user which of their lines would be replaced, and ask whether to overwrite. Only on a yes, run `update` again with `--overwrite-edits` appended. On a no, suggest they move the rules they changed to outside the markers and run update again.
4. Report what changed, and that nothing outside the markers was touched.

## When the script refuses

- Exit code 2: the markers are damaged (one is missing, doubled, or they are in the wrong order). Relay the message with its line numbers and stop. Do not repair the file yourself; the user fixes or deletes the marker lines and runs the command again.
- Exit code 1: relay the message.
