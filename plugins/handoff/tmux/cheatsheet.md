# tmux for a chain of Claude Code sessions

tmux keeps terminal sessions running in the background and shows several at once. The handoff
plugin starts each next session of a story in that story's **tab** (a tmux window), tiled up to
9 **panes**, in launch order: 1 top left, 2 to its right, and so on.

Every key below starts with the **prefix**: press Ctrl-b, let go, then the key.

## Get in and out

| Do | Type |
|---|---|
| Start tmux | `tmux new -s work` |
| Leave it running in the background (detach) | prefix `d` |
| Come back to it | `tmux attach` (or `tmux a`) |
| See what is running | `tmux ls` |

Sessions keep working while you are detached. Closing the terminal window is a detach too.

## Move around

| Do | Type |
|---|---|
| Every Claude session, waiting ones first; type a number to jump | prefix `a` |
| Go to tab 1 to 9 | prefix `1` … `9` |
| Next or previous tab | prefix `n` / `p` |
| Focus a pane | click it, or prefix and an arrow key |
| Make this pane fill the tab, and back | prefix `z` |
| Scroll back | the mouse wheel; `q` leaves scroll mode |
| This sheet | prefix `H` |

## What the status bar says

- **⚑ 2 need you** on the right: two sessions wait on you (a question or a permission).
- **⚑1** after a tab's name: one of them is in that tab.
- A story tab's panes show their slot, the session's name and its state on the top border:
  working, idle, or NEEDS YOU.

It refreshes every 15 seconds. Sessions started in the background (`claude --bg`) have no pane,
so they are not counted here; `prefix a` lists them at the bottom.

## Close things

| Do | Type |
|---|---|
| End a Claude session | `/exit` in it, or Ctrl-d twice |
| Close a pane that is just a shell | `exit`, or prefix `x` then `y` |
| Close a whole tab | prefix `&` then `y` (ends every session in it) |

Before closing a story's tab, check its last session wrote its close (`/handoff:close`): the story
file `~/.claude/stories/<story>.md` is the record of what happened.

## Your own settings

The kit lives in `~/.config/flowkit/tmux.conf` and is rewritten at every Claude Code session
start. To change something, add it to your own `~/.tmux.conf` **below** its `source-file` line,
for example a different prefix:

```
unbind C-b
set -g prefix C-a
bind C-a send-prefix
```

Reload after editing: `tmux source-file ~/.tmux.conf`.
