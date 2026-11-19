---
name: start
description: Turn THIS session into the coordinator of a story that spans several repos or branches - read the mission from a file, a plan-doc section or pasted text, plan one worker session per workstream, confirm the plan, start the workers through handoff's flowkit-spawn with their briefs, route their reports, keep one status board, and never implement. Use when the user types /coordinate:start, or says "coordinate this", "orchestrate this story", "spin up workers", "multi-repo story".
disable-model-invocation: true
---

The session that runs this becomes the **coordinator**. Workers are ordinary Claude Code sessions,
started like any `handoff` spawn: in the story's tmux tab or in the background, with Remote
Control on, so the user can answer one in its pane or on the phone.

The helper is `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/coordinate.py"` (`coordinate.py` below).
Workers start through handoff's `flowkit-spawn`. Run `coordinate.py spawner` once and use the
path it prints (`<spawn>` below), not the bare name: a PATH inherited from an older session can
put an older handoff's copy first. Handoff's capacity script sits next to it:
`python3 "$(dirname <spawn>)/../scripts/capacity.py"` (`<cap>` below). While `~/.claude/load-hold` exists a launch waits until it is
gone: run `<spawn>` with `run_in_background: true` then. If it fails, say that coordinate needs `handoff` installed
(`claude plugin install handoff@flowkit`) and stop.

The user is **away** when the file `${CLAUDE_CONFIG_DIR:-~/.claude}/away` exists and is younger
than 12 hours (`/handoff:away`). Then ask nothing: take the recommended option at every fork,
mark it "(auto)" on the board, and never end a turn on a question.

## Rules that hold throughout

- **Never implement.** Read, plan, dispatch, track, unblock. Editing a worker's code costs the
  overview and the context that keeps a coordinator alive for days. Read other repos when you
  need to; change nothing in them.
- **Conclusions, not transcripts.** Workers report conclusions; the board holds summaries.
- **The board is the state.** Messages end with their sessions; the board file outlives them.
- **Workers talk to the coordinator, the coordinator to the user.** A worker asks the user
  directly, in its own session, only what needs a human (a domain decision, a permission, a
  device); everything across workstreams comes here. With `reporter=on` in handoff's settings the
  story also gets its reporter: it collects closes into the story file and messages no one.
- **No permission laundering.** Never ask a worker to do what was denied in this session; take it
  to the user.

## 1. Read the mission

The argument is a file (`docs/plan.md`), a section of one (`docs/plan.md#Rollout`: the text under
that heading, down to the next heading of the same level), or pasted text. Pull out:

1. the repos involved and the slice of work in each;
2. the workstreams and what each needs of another (what starts now, what starts on another's
   pushed branch, what starts only once something is green or merged);
3. the decisions already made, which go into every brief word for word;
4. the open questions only the user can answer, with the workstreams they block;
5. the quality bars and traps the source records.

**If the source cannot fill all five, stop here.** Thin briefs come from thin missions: the
coordinator can only pass on the detail it was given. Either ask the user for the real source, or
write the mission doc yourself (repos, workstreams, waits, decisions, traps), show it, and
coordinate from the confirmed doc, saved next to the story's other docs. Away, write it, mark it
"(auto)", and go on. Workers are briefed from a document, never from this conversation.

## 2. Plan the workers

- One worker per workstream per repo.
- A workstream that needs another's pushed code does not wait for it to land: it starts on that
  pushed branch, its Draft MR targets that branch, and it merges the base in when the base moves;
  its brief's branch line says all three. At most two unmerged bases deep (C on B on A; a fourth
  waits until A merges). A base that goes red is fixed in the base's story, never in the
  dependent's: the dependent's watch waits for it (`waiting-base`).
- "When X lands, start Y" is a watch with an on-green prompt, never a held worker. Write Y's
  brief now; the close that registers X's watch (the message that tells X to close says so) adds
  `--on-green <Y's brief> --repo <Y's tree> --on-green-name <Y's worker>`, with `--until merged`
  when Y needs X merged and not only green. For an X that no worker of yours closes, register it
  yourself, in X's pushed tree: `"$(dirname <spawn>)/flowkit-watch" add --story <slug> --mr <url>`
  with the same flags (handoff's README, "Watches"). The watch starts Y through the gate; no
  session waits for X.
- Two workers in one repo get a worktree each: `git -C <repo> worktree add ../<repo>-<slug>
  -b <branch>`, and the worker starts there. Two sessions on one checkout overwrite each other.
- The story slug (`<slug>`): short, lowercase, from the mission. Worker names
  `<slug>-<workstream>`, letters, digits and `-`.
- The board: `${CLAUDE_CONFIG_DIR:-~/.claude}/coordination/<slug>.md`; the briefs beside it in
  `coordination/<slug>/<worker>.md`.
- Model and effort: read handoff's ladder file,
  `${XDG_CONFIG_HOME:-~/.config}/flowkit/ladder.md`. If it exists, each worker's model and effort
  come from its `## Rung` section for the worker's kind (implementation, investigation, docs,
  config …), exactly as a close picks them, Why line included. Without the file, pass neither:
  the worker runs at the user's defaults. A model or effort the user names wins.
- Your own name: `coordinate.py self`. Workers message that name. If it prints that this session
  is not listed, ask the user to `/rename` it (away: stop and say so, nothing launched).
- Capacity: the machine runs a few chains at once and has one of some resources (a simulator, a
  local backend). With more workers than free slots, or any worker that needs a resource, plan
  the batch with `/coordinate:waves` (its "Plan the batch"): it gives each worker its wave and
  times, and sheds what needs no resource to a chain of its own.
- The gate: what each item waits for between a green pipeline and its merge. The repo names its
  gates in its `CLAUDE.local.md`, else its `CLAUDE.md` (`merges on green`, `device test`, `your
  look` are examples); read them there and set each item's gate on its test-queue line now, at
  planning: `<cap> testq <slug> set "<item>" --gate "<text>"` (`/coordinate:waves`, "The test
  queue"). When the repo names none, ask the user once and write the answer into its
  `CLAUDE.local.md`; away, set no gate and put the question on the board under "Waiting on the
  user". Each worker's brief carries its item's gate.

## 3. Confirm the plan

Show, before anything starts:

| Worker | Repo or worktree | Workstream | Model | Effort | Starts | Brief in one line |
|---|---|---|---|---|---|---|

Starts: `now`; `on <branch>` for a worker that builds on another's pushed branch, with its depth
(1 or 2); `watch <X>` for one a watch starts once X is green or merged. Plus the board path and
the questions that will go to the user. Ask with AskUserQuestion: start
it (recommended), change it first, or stop. **Nothing launches before a yes.** Apply edits and
go. Away, start it and mark the plan "(auto)" on the board.

## 4. Brief and start each worker

Write each brief from `brief-template.md` (next to this file) into
`coordination/<slug>/<worker>.md`. Before writing, check it answers all six, because a worker
starts with an empty context and must not need to ask anything back: **what to build · where
(files, dirs) · what not to touch · what is already decided · how to verify done · who to ask
what.** Point at the repo's docs by exact path, with a reason each matters; "read the docs" is
not a brief. The brief's CHAIN END line is the story's own, word for word from
`python3 "$(dirname <spawn>)/../scripts/handoff.py" chain-end <slug>` (handoff's script, next
to the spawner): what the story's last session leaves behind, which every worker's DONE MEANS
serves and which says whether a worker pushes. `none` means the story has no line yet: write
`none` and no worker pushes.

Every brief's header carries the worker's resources and the slices left after its first one,
which the gate reads: `Needs: simulator, backend   Left: 2` (`Needs: none` for none).

Start each worker, in plan order, all of them: the gate decides what starts now.

```sh
<spawn> --story <slug> --repo <path or worktree> --name <worker> \
  [--model <model>] [--effort <effort>] <coordination dir>/<slug>/<worker>.md
```

The brief is the worker's first prompt; its first line (`Repo: … Session: … Story: <slug>`, the
header of a handoff prompt) is what makes the worker's `/handoff:close` record in the story file
and the board show the story. The script takes the launch driver and permission
mode from handoff's settings, refuses a repo with uncommitted changes (exit 6: give the worker a
worktree, or `--force` only on the user's word) or a running namesake (exit 4), and prints
`SPAWNED … name=<worker> rc=<link>`. **Exit 11 is not a failure**: it prints `QUEUED #<n>,
~<HH:MM> · id=<id> · <why>` and the launch waits in the machine's queue, which starts it by itself
once its slot and resources are free; the board's State is `queued ~HH:MM`, and you go on with the
next worker. Never retry a queued launch; `--over-cap` only on the user's word. Exit 3 means the tmux driver and no tmux here: say so (run
this session inside tmux, or `/handoff:setup` for the background driver) and stop. Exit 8 means a
folder never trusted: the user runs `claude` there once. Put each worker's Remote Control link on
the board.

Then `coordinate.py workers <worker> ...`: each started one should read `live`. Never type into
a worker's pane; everything travels by SendMessage. Then tell the user the plan line
(`/coordinate:waves`, "Tell"), no question.

## 5. The loop

The board, rewritten (not appended) on every report:

```markdown
# <slug> - coordination board
Coordinator: <name> · Mission: <path or "pasted"> · Updated: <date time>

| Workstream | Worker | Where | State | Last report | Blocked on |
|---|---|---|---|---|---|

## Waiting on the user
- <question> (<worker>, in its session / relayed here)

## Landed
- <what merged or shipped, when>

## Sessions
- <worker>: session <session id>, <repo or worktree>
```

- **DONE**: update the board, start what builds on its pushed branch, tell the user what moved.
  While anything is queued (`<cap> status` lists `queue` lines), tell the worker to `/handoff:close` now: an idle worker holds its slot, and its close
  starts the next launch.
- **The test queue**: whenever an item becomes testable, or its state or ETA moves, set its line
  in `${CLAUDE_CONFIG_DIR:-~/.claude}/stories/<slug>.tests.md` with `<cap> testq <slug> set
  "<item>" --mr … --device … --state … --eta HH:MM` (`/coordinate:waves`, "The test queue"),
  never by hand: the drain writes the same file. One line per item, `item · MR · device · state ·
  ETA`, then its notes and the `Gate: <text>` field set at planning (the board plugin's README,
  "The test queue"); `/board` and `/where` show it under the story.
- **A close with `Next: watching <name>`** (a worker that pushed last: its close registered a
  watch on the pipeline) is the board's State `watching <name>`. The worker is gone and its slot
  is free; the watch's green, its one fix slice or its report arrives as a section of the story
  file. Never Monitor a pipeline, and never keep a worker open for one.
- **Status replies** read `<cap> status` with the board, and run the self-check
  (`/coordinate:waves`, "Self-check"). The `watch` lines of `<cap> status` list every open watch
  with its state, when it was armed and its last answer. Never poll the queue: it drains itself.
- **BLOCKED**: record it. Blocked on the user: say so plainly, with the worker's name and its
  Remote Control link.
- **To reach a worker**, `coordinate.py workers <worker>` first. `live`: SendMessage to its name;
  a busy worker gets it between tool calls. `retired`: a background session Claude stopped after
  it sat idle for hours, its conversation kept; `coordinate.py wake <worker> "<message>"` wakes it
  with the message as its prompt. `ended` or `missing`: its pane was closed or it crashed; tell the
  user, and its `claude --resume <session id>` from its repo (the Sessions list) brings it back.
- A worker silent for long: check it the same way before assuming anything.

## 6. Wind down

When every workstream reports done, or the user calls it: tell each worker to close with
`/handoff:close` (no `spawn`), which records its close in the story file. Then the final board:
every worker's state and session id, what landed, what is left for the user. Teardown is the
user's call: tmux tabs close with `tmux kill-window -t <tab>`, background workers with `claude
stop <id>`. Keep worktrees until their changes merge: review fixes are cheapest in place.
