---
name: waves
description: Run a batch of chains within what this machine can hold - plan it in waves with handoff's capacity.py (slots, shared resources like a simulator, ETAs as clock times), hand every launch to flowkit-spawn's gate in plan order (it starts what fits and queues the rest, which then starts by itself), tell the user the plan in one line, keep the test queue through capacity.py, and keep the coordinator's own context growth in check. Use when a coordinator is handed more items than fit at once, plans or re-plans a batch, gets a DONE while anything is queued, gives a status reply, or when the user types /coordinate:waves.
---

A machine runs a few chains at once (`slots`, 3 by default) and has one of some things: a
simulator, an emulator, a local backend, a dev-server port. handoff's `flowkit-spawn` is the gate
that holds every story launch to both, and its queue starts what waits on its own, on every close
and every 15 seconds in tmux. This skill is the coordinator's side: plan, hand over, tell, and
stay small. The source of truth for how the gate counts is handoff's README, "Capacity".

The helpers:

- `<spawn>`: the path `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/coordinate.py" spawner` prints.
- `<cap>`: `python3 "$(dirname <spawn>)/../scripts/capacity.py"`, handoff's capacity script next
  to the spawner. Never a second copy, never the bare `flowkit-capacity` from an old PATH.

The user is away when `${CLAUDE_CONFIG_DIR:-~/.claude}/away` exists and is younger than 12 hours:
then ask nothing, take the recommended option at every fork and mark it "(auto)".

## 1. Plan the batch

For each item: the repo or worktree it runs in, how many slices it takes, and which resources any
of its slices needs (the names `<cap> status` lists under `resource`). An item that needs none is
**shed**: it gets a chain of its own (its own story slug, its own reporter and board card, no
`Report-to` back here) and leaves this board once handed over, so this coordinator stops paying
for it.

Then, in the order the items should start:

```sh
<cap> waves <name>=<tree>:<slices>:<need+need> <name>=<tree>:<slices> ... --story <slug> --write
```

It prints one line per item (wave, `now` or `start ~HH:MM`, `ready ~HH:MM`, slices, needs or
`shed`) and the plan line. The times replay the gate's own rules over the running chains (theirs
and every other story's, with the `Left:` of their latest launch), the queue and the batch, at
each repo's median slice time from the log (`slice_minutes` until a repo has history). `--write`
puts every item that does not start now into the story's test queue (`queued, wave 2, needs
simulator`, its ready time), tied to its launch name so the drain moves it to `running`. Use each
item's `<name>` as its launch name (`--name`).

## 2. Hand it to the gate

Launch every item through `<spawn>`, in plan order, shed items under their own `--story`. Every
brief's header (the lines before its first blank line) carries the item's resources and the
slices left after this one, which the gate reads:

```
Repo: <path>   Session: <name>   Story: <slug>
Needs: simulator, emulator   Left: 2
```

`Needs: none` (or no line) needs nothing. Exit 0 with `SPAWNED`: started. **Exit 11 with
`QUEUED #<n>, ~<HH:MM> · id=<id> · <why>` is not a failure**: the launch waits in the machine's
queue and starts by itself when its slot and resources free up; put `queued ~HH:MM` on the board
and go on with the next item. Never retry a queued launch, never wait for it, and pass
`--over-cap` only on the user's word.

## 3. Tell, in one line, no question

Right after handing over, the plan line from `waves`, as it printed:

```
Plan: 3 of 7 now (A, B, C) · 2 queued (D ~16:40, E ~18:10) · 2 shed (F, G) · slots 3/3 · 4 wait on your testing
```

Every time is a forecast and keeps its `~`. Nothing in it is a question: the gate has already
decided.

## 4. The test queue

Every write to `${CLAUDE_CONFIG_DIR:-~/.claude}/stories/<slug>.tests.md` goes through
`<cap> testq`, never a rewrite by hand: it takes the lock (the drain writes the same file) and
logs each change, which is how planned and actual ready times are measured.

```sh
<cap> testq <slug> set "<item>" --mr '!93' --device "iOS sim" --state "ready to test" --eta ""
<cap> testq <slug> set "<item>" --state building --eta 15:10
<cap> testq <slug> set "<item>" --gate "device test"
<cap> testq <slug> drop "<item>"
<cap> testq <slug>
```

A line is `item · MR · device · state · ETA`; what follows are notes, kept as written, and the
item's **gate**, a field `Gate: <text>`: what it waits for between a green pipeline and its merge.
Set every item's gate at planning, right after `waves` (`--write` writes only the items that wait,
and no gate), with `testq set --gate`. Which gates a repo has is the repo's to say: read them in
its `CLAUDE.local.md`, else its `CLAUDE.md` (`merges on green`, `device test`, `your look` are
examples, not a list). When neither names them, ask the user once and write the answer into the
repo's `CLAUDE.local.md`, so the next plan reads it there; away, set no gate and put the question
on the board.

A gate that starts `merges on green` waits on nobody; any other gate, and a line without one,
counts toward the plan line's "wait on your testing" once its state has `to test` in it. `device`
in a gate marks a device test. The file goes when its last line does.

## 5. The loop

- **A worker's DONE while anything is queued** (`<cap> status` lists `queue` lines): tell it to
  `/handoff:close` now. An idle worker holds its slot, and its close is what starts the next one.
- **A close with `Next: watching <name>`** is the board's State `watching <name>`: a script
  watches that pipeline (handoff's README, "Watches") and the slot is free already. Never Monitor
  a pipeline.
- **Status replies** read `<cap> status` (slots, chains, resources, the queue with its ETAs, and
  a `watch` line per open watch: its state, when it was armed, its last answer) and the board.
  Never poll and never sleep on the queue: it drains itself.
- **A batch added mid-way**: plan it with `waves` the same way; the queue already holds what
  waits, ahead of it.
- A `reclaim` (a holder died) or a `stalled` chain in `<cap> status`: say so in the next status
  line, with the name.

## 6. Self-check, at every status reply

```sh
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/coordinate.py" self --growth
```

prints `growth: 412k in 24 h (2 compactions, now 120k) · over 300k`: the context this session
added in the last 24 hours (what its compactions dropped plus its size now), against
`coordinator_growth` in capacity.conf. Over: say so with the number, shed every item still
sheddable, and run one repo slot fewer until it falls:

```sh
<cap> limit <repo> -1 --hours 12
```

(`<cap> limit <repo> 0` ends it early.) Under: nothing to say.
