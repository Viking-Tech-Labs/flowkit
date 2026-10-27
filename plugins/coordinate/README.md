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

## The mission

A file (`docs/plan.md`), one section of it (`docs/plan.md#Rollout`), or text you paste. It must
say which repos, which workstreams and what each waits on, what is already decided, what only you
can answer, and the traps. When it cannot, the coordinator asks for the real source or writes the
mission doc itself and shows it to you first: workers are briefed from a document, never from the
coordinator's conversation. coordinate names no issue tracker; to start from a ticket, have your
own tracker tools write it into a file first.

## Workers

- One worker per workstream per repo, named `<story>-<workstream>`; two in one repo get a git
  worktree each, since two sessions on one checkout overwrite each other.
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
releases what was held on it; a BLOCKED on you names the worker and its Remote Control link. At
the end the coordinator tells each worker to close with `/handoff:close`; closing tabs and
stopping sessions stays your call.

Workers sit idle by design, waiting for the next message. In tmux they wait as long as their pane
is open. A background session that sits idle for hours is stopped by Claude, its conversation
kept; the coordinator checks before each message (`coordinate.py workers`) and wakes a stopped
worker with the message as its prompt (`coordinate.py wake`), under its own id and settings.

While you are away (`/handoff:away`) the coordinator asks nothing: it takes the recommended
option at each fork and marks it "(auto)" on the board.
