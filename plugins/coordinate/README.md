# coordinate

Turns a Claude Code session into the coordinator of a story that spans several repos or branches.
It reads the mission from a document, plans one worker session per workstream, starts them the
way [`handoff`](../handoff/README.md) starts any session, routes their reports, and keeps one
status board. It never implements anything itself: a coordinator that edits code loses the
overview and the context it needs to last for days.

Install, after adding the flowkit marketplace (see the root README): `claude plugin install
coordinate@flowkit`. It needs `handoff`, whose `flowkit-spawn` starts every worker.

| Skill | What it does |
|---|---|
| `/coordinate:start <mission>` | Makes this session the coordinator: reads the mission, plans the workers, shows the plan and starts nothing before your yes, briefs and starts each worker, then runs the loop below. |
| `/coordinate:waves` | Runs a batch within what the machine can hold: plans it in waves, hands every launch to `flowkit-spawn`'s gate, tells you the plan in one line, keeps the test queue and the coordinator's own growth in check (see "Waves" below). The coordinator reaches for it on its own. |

## The mission

A file (`docs/plan.md`), one section of it (`docs/plan.md#Rollout`), or text you paste. It must
say which repos, which workstreams and what each needs of another, what is already decided, what only you
can answer, and the traps. When it cannot, the coordinator asks for the real source or writes the
mission doc itself and shows it to you first: workers are briefed from a document, never from the
coordinator's conversation. coordinate names no issue tracker; to start from a ticket, have your
own tracker tools write it into a file first.

## Workers

- One worker per workstream per repo, named `<story>-<workstream>`; two in one repo get a git
  worktree each, since two sessions on one checkout overwrite each other.
- A workstream that needs another's pushed code starts on that pushed branch instead of waiting
  for it to land: its Draft MR targets that branch and it merges the base in when the base moves,
  at most two unmerged bases deep. A base that goes red is fixed in the base's own story.
- "When X lands, start Y" is a watch with an on-green prompt (handoff's README, "Watches"), never
  a worker held idle: Y's brief is written at once, and the watch on X starts it through the gate
  once X is green, or merged with `--until merged`.
- Each starts through `flowkit-spawn --story <story>` with its brief as its first prompt (kept in
  `~/.claude/coordination/<story>/<worker>.md`), so it follows your `handoff` settings: the tmux
  driver puts the workers in the story's tab, the background driver starts them with `claude
  --bg`; Remote Control is on, and the same refusals apply (uncommitted changes, a running
  namesake).
- Each brief carries the story's `Chain end:` line next to its DONE MEANS, word for word from
  `handoff.py chain-end <story>` (handoff 0.7.3's line: what the story's last session leaves
  behind), so a worker pushes only as the story says; `none` when the story has no line yet.
- Model and effort come from the `## Rung` table of `~/.config/flowkit/ladder.md` when you have
  one (the `ladder` plugin writes one), as a close picks them; without it, your defaults.
- A worker asks you, in its own session, only what needs a human; everything across workstreams
  goes to the coordinator by message. With `reporter=on` the story's reporter still collects the
  closes, and messages no one.

## The loop

The board, `~/.claude/coordination/<story>.md`, is rewritten on every report: each workstream's
state and last report, what waits on you, what landed, and each worker's session id. A DONE
starts what builds on its pushed branch; a BLOCKED on you names the worker and its Remote Control
link. At the end the coordinator tells each worker to close with `/handoff:close`; closing tabs
and stopping sessions stays your call.

Nobody waits for a pipeline (handoff 0.9.0). A worker that pushed last closes with a watch on its
merge request, recorded as `Next: watching <name>`: the board's State is `watching <name>`, the
worker's slot is free, and green, the one fix slice of a first red, or a report arrives in the
story file. The coordinator never Monitors a pipeline; its status replies read `flowkit-capacity
status`, which lists every open watch with its state, when it was armed and its last answer.

Workers sit idle by design, waiting for the next message. In tmux they wait as long as their pane
is open. A background session that sits idle for hours is stopped by Claude, its conversation
kept; the coordinator checks before each message (`coordinate.py workers`) and wakes a stopped
worker with the message as its prompt (`coordinate.py wake`), under its own id and settings.

## Waves

A machine runs a few chains at once and has one of some things (a simulator, a local backend):
handoff's README, "Capacity", says how `flowkit-spawn` counts and holds both. The coordinator
plans a batch with `capacity.py waves` (each item's wave, start and ready times as `~HH:MM`, from
each repo's median slice time in the log), sheds every item that needs no resource to a chain of
its own, and launches everything in order: the gate starts what fits and queues the rest (exit
11, `QUEUED #2, ~16:40`), and the queue starts it by itself as slots free up. You get one line:

```
Plan: 3 of 7 now (A, B, C) · 2 queued (D ~16:40, E ~18:10) · 2 shed (F, G) · slots 3/3 · 4 wait on your testing
```

- Briefs carry `Needs:` and `Left:` in their header, which the gate reads.
- A worker's DONE while anything is queued gets told to close at once: an idle worker holds its
  slot.
- The test queue (`<story>.tests.md`) is written only through `capacity.py testq`, locked and
  logged, so planned and actual ready times can be compared; queued items get their lines from
  `waves --write` and move to `running` when they start. Each item's gate (`Gate: <text>`: what
  it waits for between green and its merge) is set at planning with `testq set --gate`, from the
  gates the repo names in its `CLAUDE.local.md` or `CLAUDE.md`; when it names none the
  coordinator asks once and writes the answer there.
- At each status reply the coordinator checks its own context growth over the last 24 hours
  (`coordinate.py self --growth`); over `coordinator_growth` it says so, sheds what it can and runs
  one repo slot fewer for a while (`capacity.py limit <repo> -1`).

While you are away (`/handoff:away`) the coordinator asks nothing: it takes the recommended
option at each fork and marks it "(auto)" on the board.

Reminders of what waits on you are not the coordinator's: with `remind=on` in `handoff.conf`
(handoff 0.10.0) the tmux kit's tick sends them (the handoff README, "Reminders"), and the
coordinator keeps no reminder timer of its own.
