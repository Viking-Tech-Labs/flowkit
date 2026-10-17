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
put an older handoff's copy first. If it fails, say that coordinate needs `handoff` installed
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
2. the workstreams and what each waits on (what starts now, what is held until something lands);
3. the decisions already made, which go into every brief word for word;
4. the open questions only the user can answer, with the workstreams they block;
5. the quality bars and traps the source records.

**If the source cannot fill all five, stop here.** Thin briefs come from thin missions: the
coordinator can only pass on the detail it was given. Either ask the user for the real source, or
write the mission doc yourself (repos, workstreams, waits, decisions, traps), show it, and
coordinate from the confirmed doc, saved next to the story's other docs. Away, write it, mark it
"(auto)", and go on. Workers are briefed from a document, never from this conversation.

## 2. Plan the workers

- One worker per workstream per repo. A workstream held until another lands, with nothing else
  to do, starts when that lands, not now.
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

## 3. Confirm the plan

Show, before anything starts:

| Worker | Repo or worktree | Workstream | Model | Effort | Held until | Brief in one line |
|---|---|---|---|---|---|---|

plus the board path and the questions that will go to the user. Ask with AskUserQuestion: start
it (recommended), change it first, or stop. **Nothing launches before a yes.** Apply edits and
go. Away, start it and mark the plan "(auto)" on the board.

## 4. Brief and start each worker

Write each brief from `brief-template.md` (next to this file) into
`coordination/<slug>/<worker>.md`. Before writing, check it answers all six, because a worker
starts with an empty context and must not need to ask anything back: **what to build · where
(files, dirs) · what not to touch · what is already decided · how to verify done · who to ask
what.** Point at the repo's docs by exact path, with a reason each matters; "read the docs" is
not a brief.

Start each worker, in plan order:

```sh
<spawn> --story <slug> --repo <path or worktree> --name <worker> \
  [--model <model>] [--effort <effort>] <coordination dir>/<slug>/<worker>.md
```

The brief is the worker's first prompt; its first line (`Repo: … Session: … Story: <slug>`, the
header of a handoff prompt) is what makes the worker's `/handoff:close` record in the story file
and the board show the story. The script takes the launch driver and permission
mode from handoff's settings, refuses a repo with uncommitted changes (exit 6: give the worker a
worktree, or `--force` only on the user's word) or a running namesake (exit 4), and prints
`SPAWNED … name=<worker> rc=<link>`. Exit 3 means the tmux driver and no tmux here: say so (run
this session inside tmux, or `/handoff:setup` for the background driver) and stop. Exit 8 means a
folder never trusted: the user runs `claude` there once. Put each worker's Remote Control link on
the board.

Then `coordinate.py workers <worker> ...`: each should read `live`. Never type into a worker's
pane; everything travels by SendMessage.

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

- **DONE**: update the board, start or brief what was held on it, tell the user what moved.
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
