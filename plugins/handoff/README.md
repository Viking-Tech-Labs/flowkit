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
| `/handoff:away` | Marks you away (`~/.claude/away`, cleared by `/handoff:back` or after 12 hours): closes and sessions stop asking and take the recommended option, marked "(auto)". |
| `/handoff:back` | Clears the flag; sessions ask you again. |
| `/handoff:setup` | Offers the status line below, as a diff of your settings, and writes it only on your yes. |

Modes, combined freely: `quick` skips the docs sweep; `prompt-only` prints only the next prompt
(or the one line saying what remains); `overnight` runs unattended and sets the away flag; `spawn`
saves the prompt and prints the line that starts the next session. Any other text is what the next
session should do, passed into its prompt as written.

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
