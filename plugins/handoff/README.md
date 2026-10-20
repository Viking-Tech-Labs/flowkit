# handoff

Close a Claude Code session so the next one starts cold, with nothing lost and nothing worked out
twice: green work committed, stale docs fixed, a pick-up block written into the repo's plan doc,
the next session's prompt drafted when repo work is left, and the close recorded in the story's
file.

Install, after adding the flowkit marketplace (see the root README): `claude plugin install handoff@flowkit`.

Skills are always typed with the plugin's name in front (`/handoff:close`), so a skill of your own
called `handoff` keeps working beside this one.

| Skill | What it does |
|---|---|
| `/handoff:close [modes] [what next]` | Closes the session: commits green work (never pushes), fixes docs it made stale, writes the pick-up block, drafts the next session's prompt when repo work is left, closes with `DONE` / `FOR ME` / `OPEN` / `LEFT` and records the close in the story file. |
| `/handoff:away` | Marks you away (`~/.claude/away`, cleared by `/handoff:back` or after 12 hours): every session, running already or started later, stops asking and takes the recommended option, marked "(auto)"; a session already sitting on a question is told so at once (below). |
| `/handoff:back` | Clears the flag, so sessions ask you again, and lists the sessions waiting on you right now. |
| `/handoff:spawn [single] <prompt>` | Starts any session with a prompt the way a close's `spawn` does: in its story's tab, or with `single` as a one-off in a shared `adhoc` tab ("open a fresh session in that repo"). |
| `/handoff:setup` | Offers the status line below, as a diff of your settings, and writes it only on your yes; asks which launch driver spawned sessions use, and for background sessions offers two settings of yours. |
| `/handoff:setup-tmux` | Offers the tmux kit below and the one line that loads it from your tmux.conf, and writes only on your yes; explains the launch settings. |

Modes, combined freely: `quick` skips the docs sweep; `prompt-only` prints only the next prompt
(or the one line saying what remains); `overnight` runs unattended and sets the away flag; `spawn`
saves the prompt and starts the next session in the story's tmux tab, or in the background (below). Any other text is what the next
session should do, passed into its prompt as written.

## Starting the next session: spawn

`/handoff:close spawn` saves the next prompt and starts that session through `flowkit-spawn`
(`bin/`, on the PATH of Claude's Bash tool), with Remote Control on so you can follow it from
your phone. Two drivers, set once with `/handoff:setup`. The tmux driver:

- each story gets its own tab, named after the story; its sessions are tiled in launch order, up
  to 9, then a second tab `<story>-2`;
- it refuses when a session of that name already runs or the repo has uncommitted changes, since
  two sessions on one working tree overwrite each other's work;
- it waits until `claude agents` lists the new session and prints its Remote Control link; a
  session that does not come up in 15 seconds is most often waiting at a folder-trust prompt.

Outside tmux nothing launches: the close prints the launch line under FOR ME and records the
prompt as pending. After a launch the closing session leaves the repo alone.

The background driver (`driver=background`) starts the session with `claude --bg`, Claude's own
background sessions, so no tmux is needed:

- the session shows in `claude agents`, in the board and on your phone (Remote Control);
  `claude attach <name>` opens it in a terminal;
- the same refusals as tmux (a running namesake, uncommitted changes), plus one of its own: a
  folder you never opened in Claude refuses to start until you run `claude` there once and
  accept the trust prompt;
- with `reporter=on` the story's first launch also starts `<story>-report` in the background.
  Each close rings it: `handoff.py record` wakes it with `closed <session>` as its prompt
  (stopping it first when it sits idle, since a running background session cannot be typed into);
- a background session idle at its prompt for some hours (8 h on Claude Code 2.1.301; the
  number changes between versions) is stopped by Claude, its conversation kept: the next ring wakes a stopped reporter, and `claude attach` reopens any;
- `claude --bg` does not pass on your shell's environment; the next session gets
  `XDG_CONFIG_HOME` from the one that started it, so both read the same settings.

The settings are plain `key=value` lines in `~/.config/flowkit/handoff.conf`, outside the plugin
so an update or an uninstall keeps them, and readable by the scripts that run outside Claude:

| Key | Default | Means |
|---|---|---|
| `driver` | `tmux` | How the next session starts: `tmux` (a tab per story) or `background` (`claude --bg`). |
| `reporter` | `off` | `on` starts a `<story>-report` session in slot 1 of each new story tab (in the background with that driver): it collects the story's closes and writes one final report, with one merged FOR ME list, when the story is done. Each close rings it: `handoff.py record` types `closed <session>` into its pane, or wakes the background one with it. |
| `permission_mode` | empty | The `--permission-mode` of spawned sessions; empty keeps your default. A chain that runs while you are away needs a mode that does not stop to ask. |
| `autoclose` | `off` | `on` lets the tmux kit's status-bar tick close a finished story's tab on its own (see "The tmux kit"). `/handoff:setup` offers it. |

The model and effort of the next session come from its prompt (see "Your own ladder"), passed as
they are.

## The tmux kit

`/handoff:setup-tmux` copies a small tmux setup to `~/.config/flowkit/tmux.conf` (and
`flowkit-away`, see "Being away") and adds one line to your tmux.conf: `source-file
~/.config/flowkit/tmux.conf`. It keeps tmux's Ctrl-b prefix
and adds: mouse on, tabs and panes counted from 1, **⚑ n need you** in the status bar with ⚑n on
each tab that has sessions waiting on you, a one-word state after each story or adhoc tab's
name (`3:my-story · running`), `prefix a` to list every Claude session and jump to one, and `prefix H` for the one-page cheat sheet (`tmux/cheatsheet.md`). The conf calls the
helper at `~/.local/bin/flowkit-tmux`, a copy kept at the plugin's version by a session-start
hook, because the plugin's own directory moves with every version; the hook does nothing until
you install the kit. Put your own changes below the `source-file` line: a setting there wins.
Sessions started in the background have no pane and are not counted; `prefix a` lists them.

The tab's word is its most urgent session's state, in this order: **needs you** (a question or a
permission), **error** (the last turn failed on an API error), **stalled** (idle 15 minutes or
more, transcript and forks untouched, no close recorded), **running**, **watching** (closed, but a
watch or background task still runs), **done** (closed and idle). A close counts once the story
file has its `Session:` line. Your own windows, without a story or adhoc mark, get no word.

New in 0.5.0, off until you turn it on: with `autoclose=on` in `handoff.conf`, the same tick
closes a story's tab once the story is finished, so finished tabs no longer pile up. A story is
finished when its reporter's final report is in, or, with no reporter left, when every session a
close launched has recorded its own close; while the closes are still missing, the tick types
`report now` into an idle reporter (at most every 5 minutes) so it writes that report. The whole
story goes at once, every pane in every one of its tabs, the reporter last, and only when nothing
in it is busy, waiting on you, watching or in error, no pane runs anything but Claude or a bare
shell, the story file has sat 5 minutes and every session's transcript and forks have been still
for 5 minutes. A tab whose sessions all exited closes too. `tmux set-option -w @flowkit_hold 1`
in a tab keeps its story open; the adhoc tab and your own windows are never closed.

## Being away

`/handoff:away` writes `~/.claude/away` (the stamp of when; it counts for 12 hours) and makes it
reach every session, in two ways:

- **No session asks you anything.** A PreToolUse hook on AskUserQuestion (`hooks/hooks.json`)
  denies the question while the flag is set, with a reason the session acts on: take the
  recommended option, mark it "(auto)" in its decision log and its close, carry on, never end a
  turn on a question. The harness enforces it in every session that loads the plugin, hand-started
  or spawned, in a pane or in the background, so nothing depends on a session remembering to read
  the flag, and `/handoff:back` has nothing to announce: once the flag is gone, questions go
  through again.
- **A session already sitting on a question is nudged.** `bin/flowkit-away nudge` reads `claude
  agents --json`; each session with `status: waiting` on `input needed` gets the away line: in a
  tmux pane, Escape ends the question's turn and the line is typed as its next prompt (as the
  reporter doorbell types); a background session is stopped and resumed with the line as its
  prompt. One line per session comes back: `nudged`, `permission` (a permission prompt is never
  answered, since nothing here approves on your behalf: answer it from the phone), `unreachable`
  (a question in a plain terminal outside tmux), `skipped` (a `<story>-report`, which never asks).
  Idle and busy sessions are left alone, since the hook catches their next question; a session
  that ended its turn on a question in prose stays as it is.

`/handoff:setup-tmux` copies `flowkit-away` to `~/.local/bin` beside `flowkit-tmux`, kept at the
plugin's version at every session start, so a shell function of your own that writes the flag can
call `flowkit-away nudge` and `flowkit-away waiting` too (`--dry-run` shows what `nudge` would do,
`--only <name>` limits it to one session). The flag is the record and the nudge a courtesy: a
session the nudge could not reach still reads the flag at its next question.

## Keeping the computer awake

The plugin's hooks hold a `caffeinate -i` while a turn runs, and between turns while Remote Control
is attached or you are away, so a chain of sessions keeps going overnight. Each hold is bound to its
Claude process and ends with it. The display still sleeps, and **a closed laptop lid still sleeps
the computer and pauses the chain**: leave the lid open and the power connected. Where `caffeinate`
does not exist (anything but macOS) the hooks do nothing.

## The status line

Claude Code hands a session's context use and cost only to the status line, so a close cannot see
them unless one records them. `/handoff:setup` offers one that does: on every refresh it writes
`~/.claude/session-stats/<session id>.json` (and `by-name/<name>.json` for a named session) with
the model, effort, cost, API time, context used and lines changed, and shows `Opus high · ctx 34% ·
$1.23`. If you already have a status line, yours keeps showing: the new command records the
numbers, then runs yours. The script is copied to `~/.config/flowkit/statusline.py` and kept at the
plugin's version by a session-start hook, which does nothing when you have not installed it. Your
previous settings are kept as `settings.json.flowkit-bak`.

## What a close writes

- **The pick-up block**, in the repo's own plan doc under `### WHERE TO PICK UP`, replacing the
  previous one. Which doc is found once per repo (an existing pick-up block, or a `PLAN.md`-like
  file) or asked once, and remembered in the plugin's data directory. The block's shape is
  `skills/close/pickup-template.md`.
- **The next session's prompt**, only when repo work is left: repo, session name, story, the
  sections to read, verified facts, today's items with their checks, the fences and the stop
  condition. Its shape is `skills/close/prompt-template.md`. A prompt that nothing started is
  saved under `~/.claude/spawned/<name>.md`.
- **The story file**, `~/.claude/stories/<slug>.md`, when the session's prompt names a `Story:`:
  one section per close with the session id, how to resume it, the four close blocks and a
  `Next:` line: `started <name>`, `pending <name>` (a saved prompt nothing started) or `none`.
  `Next: none` is written only when no repo work is left; on the last close, with no session of
  the story still live, it is what marks the story done.

Unattended (`overnight`, the away flag set, or a headless run without the question tool) the close
never asks: it takes the recommended option at every fork and marks it "(auto)".

## Your own ladder

A close can choose the next session's model and effort, and run a step of yours, from one file:
`~/.config/flowkit/ladder.md` (`$XDG_CONFIG_HOME/flowkit/ladder.md` when that is set). It lives
outside the plugin so an uninstall or an update never touches it. Without the file the close skips
this step and the next prompt carries no model or effort: the next session runs at your defaults.

The file is plain instructions in two sections, which the close reads and follows:

- `## Rung`: how to pick Model, Effort and a one-line Why for the next session from its kind
  (planning, investigation, implementation, review, docs or config).
- `## Close step`: what to do at every close, before the story record. It may run your own
  scripts; give them by absolute path.

A model or effort you name in the session wins over the file. A worked example, a fixed table
plus a log line:

```markdown
## Rung

| Kind | Model | Effort |
|---|---|---|
| planning | opus | high |
| investigation | opus | medium |
| implementation | sonnet | high |
| review | opus | medium |
| docs or config | sonnet | medium |

Why line: "<kind> from the fixed table".

## Close step

Append one line to `~/notes/sessions.log`:
`<date> <this session's name> <kind> <model>/<effort> next=<next session's name or none>`.
```
