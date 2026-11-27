# A day in the flow

One working day with the chain plugins (`handoff`, `board`, `ladder`, and `coordinate` when a
story spans several repositories), from a story in the morning to its merge request merging
overnight. Every command here is in a plugin's README or skill; the README of each plugin has the
details. Setup is in the top README, "Chains of sessions".

## 1. The story

flowkit names no issue tracker. A story reaches a session through your own tools (an MCP server
or a skill for your tracker), or through text you paste. What flowkit adds is a name: a session
whose prompt carries a `Story: <slug>` line belongs to that story, and every close writes into
`~/.claude/stories/<slug>.md`. That file is the story's memory across sessions, and what the board
reads.

## 2. The plan

There is no plan command; you plan in a session the way you like, and the plan lives in the
repository's plan doc. For a story across several repositories, `/coordinate:start <mission>`
makes the session a coordinator: the mission is a file (`docs/plan.md`), one section of it
(`docs/plan.md#Rollout`) or pasted text, and it plans one worker per workstream, each with a
model, an effort and a one-line brief. `/coordinate:waves` plans a larger batch in waves that fit
your machine's slots. For a fork that is expensive to reverse, the `session-discipline` plugin's
decision-interview skill asks it as choices with a recommendation, rather than guessing.

## 3. The approval

Nothing launches before you say yes. A coordinator asks once: start it, change it first, or stop.
The close that follows an approved plan asks one more thing, once per story: where the chain ends.
Its answer is the story's `Chain end:` line (`branch`, `draft-mr`, `mr-ready` or `merge`), and
every slice after it reads that line to know what the last slice leaves behind: the branch, a
Draft MR, an MR ready for review, or an MR set to merge. `/handoff:setup` lets you set a default
(`chain_end=`).

## 4. The chain

Work runs in slices, each sized to finish well inside one session's context. A slice ends with
`/handoff:close spawn`: it commits green work, writes the pick-up block (`### WHERE TO PICK UP`)
into the repository's plan doc, drafts the next session's prompt, records the close in the story
file with a `Next:` line, and starts the next session in the story's tmux tab (or as a background
session, if you chose that in `/handoff:setup`). `/handoff:close` without `spawn` stops there and
gives you the prompt; `/handoff:spawn <prompt>` starts any other session. The model and effort of
each launch come from your ladder (`/ladder:setup`, `/ladder:edit`).

`/board` shows a card per story: running, needs you, stalled or done, with its FOR ME steps.
`/where` and `/forme` print the same as text.

## 5. Away

Before you leave a chain running, type `/handoff:away` (or close the last slice of the evening
with `/handoff:close overnight spawn`). It takes effect five minutes later, after a last call to
any idle coordinator: from then on no session asks you anything; each takes the recommended option
and marks it "(auto)". It also prints how much of your plan's usage is left. The computer is kept
awake, but a closed laptop lid still puts it to sleep and pauses the chain. Optional switches in
`/handoff:setup`: `autoretry=on` carries on after an API error, `limitresume` (on by default)
continues a session a usage limit stopped once the limit resets, `remind=on` pushes what waits on
you, `idle_release=on` (macOS) clears a forgotten away flag at your first prompt back at the
keyboard.

Going out with the phone instead? `/handoff:awake` keeps the computer up the same way, and the
sessions keep asking you, over Remote Control.

## 6. The morning

`/handoff:back` clears the flag and lists every session waiting on you right now, or says nobody
is. What the sessions decided alone is marked "(auto)" in the closes in the story files and in the
pick-up blocks, for you to review. `/board` (or `/forme`) shows the FOR ME steps the night left.

## 7. The test queue

When a change needs your hands on a device, the story's test queue says what is ready:
`flowkit-capacity testq <story>` keeps `~/.claude/stories/<story>.tests.md`, one line per item
with its MR, device, state and ETA, and the board shows it on the story's card. When you start
testing, say so: the session you tell runs `flowkit-capacity hold <resource> --for me --minutes
60`, so no slice takes that simulator or backend from under you, renews it while you test and
gives it back with `flowkit-capacity release --for me`. `flowkit-capacity status` shows who holds
what.

## 8. The merge

Nobody waits for a pipeline. The chain's last slice pushes as its `Chain end:` line says, and its
close registers a watch (`flowkit-watch add --story <slug> --mr <url>`, with `--ready` for
`mr-ready` and `--until merged` for `merge`), records `Next: watching <name>` and ends. A script
asks the pipeline once a minute and writes what it finds into the story file: green is
`Next: none`; the first red of a merge request starts one fix slice, marked "(auto)"; a second
red, or a pipeline that never starts, is reported to you there. With `--until merged` the watch
also reports the merge and the target branch's pipeline afterwards. `flowkit-watch list` shows the
open watches, and the board shows them on the cards.

The story is done when its last close says `Next: none` and no session of it is still running.
