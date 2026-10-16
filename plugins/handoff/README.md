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

Modes, combined freely: `quick` skips the docs sweep; `prompt-only` prints only the next prompt
(or the one line saying what remains); `overnight` runs unattended and sets the away flag; `spawn`
saves the prompt and prints the line that starts the next session. Any other text is what the next
session should do, passed into its prompt as written.

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
