---
name: spawn
description: Start a Claude Code session with a prompt through flowkit-spawn - in its story's tmux tab (or in the background with driver=background), or with `single` as a one-off in the shared adhoc tab - with Remote Control on. Use when the user types /handoff:spawn, or says "spawn it", "spawn a new session for X", "open a fresh session in <repo>", "a new session with Remote Control on named X". A close with `spawn` launches its own next session; this skill is for any other launch.
argument-hint: [single] [name=<x>] [story=<x>] [repo=<path>] [model=<x>] [effort=<x>] [report_to=<name|none>] <prompt file or text>
---

# /handoff:spawn

Arguments: `$ARGUMENTS`

Options, as words anywhere in the arguments; the rest is the prompt, or the path of a saved prompt
file:

- `single`: a one-off outside any story, in the shared `adhoc` tab. No reporter, no dirty-tree
  refusal (it may share a working tree with another session, and is told so), a taken name gets
  `-2`, and the prompt may be empty.
- `name=<x>`: the session's name. Default: `<repo folder>-<slice>` when the prompt names a slice,
  else the repo folder's name; a saved prompt from `~/.claude/spawned/` is named after its file.
- `story=<x>`: the story whose tab it joins. Default: the story tab this session runs in.
- `repo=<absolute path>`: its working directory. Default: this session's repo.
- `model=<x>`, `effort=<x>`: passed as given; without them the session runs at the user's defaults
  (or the `Model:` and `Effort:` lines of the prompt, when it carries them).
- `report_to=<name|none>`: the session the new one sends its verdict to when it is done (the plugin
  README's "A helper reports back"), or `none` for no report. Default: the `report_to` key of
  `~/.config/flowkit/handoff.conf` (`handoff.py state` prints it as `report-to:`): `on` is this
  session, named by `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" self` (exit 4: name this
  session with `/rename` first); a name is that session; `off`, the default, nobody. A `single`
  without a prompt carries no report whatever the key says: there is no task to report on.

1. **The prompt.** A path to an existing file: use it. Text: save it with
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" save-prompt <name>`, the text on stdin; it
   prints the saved path. A story session starts cold and has only its prompt: never launch one on
   a one-line prompt. Draft it from the repo's pick-up block with the close's
   `prompt-template.md` (in `${CLAUDE_PLUGIN_ROOT}/skills/close/`) when the text is only a slice
   name. With a report (the `report_to` option above), save with `save-prompt <name> --report-to
   <session>`, a file too (the file on stdin; one from the store under that name is rewritten in
   place): it puts `Report-to: <session>` into the prompt's header, on the `Repo:` line of a
   brief or as a first line of its own, and leaves a prompt whose header already names a
   `Report-to:`, or that carries a coordinator's `REPORTING` line, as it is.
   **A command** (the prompt's first line starts with `/`, a skill only the user can start, as in
   the close's "Command-first"): the prompt is that one line, nothing after it, since every word
   after the command reaches it as arguments; `save-prompt` refuses more lines and adds no
   `Report-to:` (it says so on stderr). The brief, with any `Report-to:`, goes into the `Brief:`
   line of the repo's pick-up block, where `flowkit-spawn` tells the new session to read it; the
   one-line rule above does not apply.
2. **Launch**, absolute paths, no `cd`:
   ```sh
   "${CLAUDE_PLUGIN_ROOT}/bin/flowkit-spawn" --repo <repo> --name <name> [--single] \
     [--story <slug>] [--model <model>] [--effort <effort>] [<saved path>]
   ```
3. **Relay its last line** in plain words: `SPAWNED …` gives the tab, the slot and the Remote
   Control link (a `REPORTER` line before it means a new story tab with its reporter in slot 1);
   with `try=2` (claude exited at start-up once, stderr said `warning: session '<name>' exited at
   start-up in <pane>; typed again`, and the second launch came up) add "launched at the second
   try". `SPAWNED driver=background …` gives its id and link, and `claude attach <name>` opens it.
   A `warning: no session … came up` means it most likely waits at a folder-trust prompt: tell the
   user which tab and slot to look at, then look at `claude agents --json` and the pane
   (`tmux capture-pane -p -t <id>`) yourself: a pane at a shell may be relaunched through
   `flowkit-spawn` again without `--pane` (exit 4 guards against a namesake); a pane at a prompt
   is the user's to answer; never a launch typed by hand, since a window opened by hand carries
   no story mark and gets no labels, no autoclose and no reporter. Exit 10, stderr `session
   '<name>' exited twice at start-up in pane <id>; its output is in the pane`: the launch was
   typed twice and claude exited both times, nothing runs in that pane (a `REPORTER` line before
   it means the reporter came up and runs): say so, name the tab and slot, and give the user the
   launch line. Exit 3 (not inside tmux): give the user the printed launch
   line. Exit 8 (background, a folder never trusted): the user runs `claude` there once and
   accepts the prompt, then you launch again. Exit 4 (that name runs) or 6 (uncommitted changes):
   say so and stop; never add `--force`
   on your own, since two sessions on one working tree overwrite each other's work.

4. **The report.** When the launched session's message arrives (a SendMessage from the session
   named in the `SPAWNED` line), relay it to the user in plain words: the verdict line, the
   findings by number, the links; then act on it as the user's rules say (a worth-fixing finding
   goes to the story's next slice). A helper whose message never arrives (held for the receiver's
   approval, or a send that failed) put the verdict in its DONE: read it in its tab.

A story session launched here owns its repo from then on: this session makes no more edits or
commits there.
