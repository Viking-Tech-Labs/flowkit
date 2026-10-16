---
name: setup-tmux
description: Offer the flowkit tmux kit for running a chain of sessions in tmux - mouse on, tabs counted from 1, a status-bar count of sessions waiting on you, prefix a to list and jump to every session, a cheat sheet - plus the launch settings of /handoff:close spawn. Shows the one line it adds to the user's tmux.conf and writes only on a yes. Use when the user types /handoff:setup-tmux, or asks how to see their spawned sessions in tmux.
disable-model-invocation: true
---

Set up tmux for `/handoff:close spawn`, which starts each next session of a story in that
story's tmux tab. A plugin cannot change the user's tmux.conf itself; this skill shows the change
and makes it only when the user says yes.

The helper is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py"` (`handoff.py` below).

## The kit

1. Run `handoff.py tmux diff "${CLAUDE_PLUGIN_ROOT}"`. If it says `installed`, say so, go to "The
   launch settings".
2. Show the user what it printed, and the kit itself: read
   `${CLAUDE_PLUGIN_ROOT}/tmux/tmux.conf` and show it whole. Say in plain words what changes:
   - three files are copied: the kit conf to `~/.config/flowkit/tmux.conf`, its cheat sheet next
     to it, and the `flowkit-tmux` helper to `~/.local/bin/` (a stable path, because the
     plugin's own directory moves with every version). They are kept at the plugin's version at
     every session start, so changes of their own go in the user's tmux.conf, below the line;
   - one line, `source-file ~/.config/flowkit/tmux.conf`, is appended to their tmux.conf, with
     a backup next to it as `<name>.flowkit-bak`. What it turns on: mouse, tabs and panes counted
     from 1, a status-bar count of sessions waiting on them, `prefix a` to list and jump to every
     session, `prefix H` for the cheat sheet. The prefix stays as it is (tmux's default Ctrl-b).
     Settings of theirs above the line that the kit also sets (mouse, status-right, the two
     window-status formats, the `a` and `H` keys) are overridden; moving them below keeps theirs.
3. Ask with AskUserQuestion: install (recommended), or leave tmux as it is. Without the
   AskUserQuestion tool (a headless run), write nothing: print what step 1 printed and the
   command that would install it, `handoff.py tmux install <plugin root>`, and stop.
4. On a yes, run `handoff.py tmux install "${CLAUDE_PLUGIN_ROOT}"` and relay its lines: a running
   tmux picks the kit up with the `tmux source-file` command it prints, which the user runs (it
   reloads every client of their tmux). On a no, write nothing.

To undo it, the user deletes the `source-file` line (or restores the backup).

## The launch settings

`/handoff:close spawn` reads `~/.config/flowkit/handoff.conf`, plain `key=value` lines:

- `driver=tmux`: how the next session starts. `tmux` is the only driver so far.
- `reporter=off`: `on` starts a reporter session in slot 1 of each new story tab, which collects
  the story's closes and writes one final report when the story is done.
- `permission_mode=`: the `--permission-mode` of spawned sessions; empty leaves the user's own
  default. A chain that runs while they are away needs a mode that does not stop to ask.

Only when the file does not exist and the AskUserQuestion tool is there: offer to create it with
the three defaults above (recommended), the user's choices folded in. Headless, never create it;
without the file every setting has the default shown.
