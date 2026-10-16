---
name: spawn
description: Start a Claude Code session with a prompt through flowkit-spawn - in its story's tmux tab, or with `single` as a one-off in the shared adhoc tab - with Remote Control on. Use when the user types /handoff:spawn, or says "spawn it", "spawn a new session for X", "open a fresh session in <repo>", "a new session with Remote Control on named X". A close with `spawn` launches its own next session; this skill is for any other launch.
argument-hint: [single] [name=<x>] [story=<x>] [repo=<path>] [model=<x>] [effort=<x>] <prompt file or text>
---

# /handoff:spawn

Arguments: `$ARGUMENTS`

Options, as words anywhere in the arguments; the rest is the prompt, or the path of a saved prompt
file:

- `single`: a one-off outside any story, in the shared `adhoc` tab. No reporter, no dirty-tree
  refusal (it may share a working tree with another session, and is told so), a taken name gets
  `-2`, and the prompt may be empty.
- `name=<x>`: the session's name. Default: `<repo folder>-<slice>` when the prompt names a slice,
  else the repo folder's name.
- `story=<x>`: the story whose tab it joins. Default: the story tab this session runs in.
- `repo=<absolute path>`: its working directory. Default: this session's repo.
- `model=<x>`, `effort=<x>`: passed as given; without them the session runs at the user's defaults
  (or the `Model:` and `Effort:` lines of the prompt, when it carries them).

1. **The prompt.** A path to an existing file: use it. Text: save it with
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" save-prompt <name>`, the text on stdin; it
   prints the saved path. A story session starts cold and has only its prompt: never launch one on
   a one-line prompt. Draft it from the repo's pick-up block with the close's
   `prompt-template.md` (in `${CLAUDE_PLUGIN_ROOT}/skills/close/`) when the text is only a slice
   name.
2. **Launch**, absolute paths, no `cd`:
   ```sh
   "${CLAUDE_PLUGIN_ROOT}/bin/flowkit-spawn" --repo <repo> --name <name> [--single] \
     [--story <slug>] [--model <model>] [--effort <effort>] [<saved path>]
   ```
3. **Relay its last line** in plain words: `SPAWNED …` gives the tab, the slot and the Remote
   Control link (a `REPORTER` line before it means a new story tab with its reporter in slot 1).
   A `warning: no session … came up` means it most likely waits at a folder-trust prompt: tell the
   user which tab and slot to look at. Exit 3 (not inside tmux): give the user the printed launch
   line. Exit 4 (that name runs) or 6 (uncommitted changes): say so and stop; never add `--force`
   on your own, since two sessions on one working tree overwrite each other's work.

A story session launched here owns its repo from then on: this session makes no more edits or
commits there.
