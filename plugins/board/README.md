# board

A live board of your story chains. `/board` opens a Pane with one card per story: what runs, what
needs you, what has **stalled**, what is done, the FOR ME steps you tick off, and the merge-request
links. It refreshes every few seconds while it is open. `/where` and `/forme` print the same facts
as text, with no model turn.

Install with `claude plugin install board@flowkit`, then `/reload-plugins` or a new session, then
`/board`. It reads the story files that [`handoff`](../handoff/README.md) writes, so it is most
useful with that plugin; a file in the same format from anywhere else works too.

## What a card says

| Card | Meant when |
|---|---|
| **NEEDS YOU** | a session of the story waits on you (a question, a permission), or a pane of its tab sits at a start-up prompt (`parked at a start-up prompt: <its first line>`: no session exists yet, so none is listed), or the last close left a launch pending (`Next: pending <name>`) or a free-text `Next:` |
| **STALLED** | no session of the story is working or waiting, and a session any close promised (`Next: started …`) never wrote its own close: it never came up, ended, or sits idle at its prompt. A new session gets 10 minutes to come up first; a stalled story nobody touches for seven days folds into the "more" line |
| **running** | a session of the story is working, or the promised one is still starting, or the last close queued its successor (`Next: queued <name>`: it waits for a slot, not for you, until a session of that name comes up; one that then ends without a close stalls the story), or a watch of the story is open (`watching <name>`: a script polls a pipeline and no session waits, see "Watches") |
| **done** | a `## Final report` newer than the last close, or the last close says `Next: none` with nothing working |

A story's sessions are the names in its file: every close's section name and every name a `Next:`
line gives, plus `<story>-report`. Sessions idle at their prompt do not hold a story open. A
session that belongs to no story shows only when it needs you.

The FOR ME list is every close's steps, newest first, a step repeated in a later close folded into
that one. When a final report is the newest section, its own list stands instead. Click a `[ ]`
or its text, press its number key while the Pane has the keyboard (`ctrl+x` then `tab`, or a
click, hands it over), or Tab to it and press Enter: the tick is kept between sessions and shared
by every session that has the board open. A card with more than five steps lists the open ones;
its last line shows them all. Stories untouched for a week, and done ones after three days (unless
a step of theirs is still open), fold into one line that unfolds.

## What it reads

- `~/.claude/stories/<story>.md`: sections `## <session> · <YYYY-MM-DD HH:MM>` with a `Next:` line
  (`started`, `spawned`, `queued`, `pending`, `watching` or `none`), the FOR ME block and any
  merge-request URL.
- `claude agents --json --all`: interactive sessions by `status`, background ones by `state`.
  Called only while the board is open.
- `~/.claude/session-stats/<session id>.json`, for the context and cost figures. The `handoff`
  status-line offer writes them; a session without the file shows without the numbers.

- `~/.claude/stories/<story>.tests.md`, when it exists: the story's test queue (below).
- `~/.claude/capacity/status.json`, when it exists: the [`handoff`](../handoff/README.md) capacity
  gate's snapshot, for the CAPACITY block and the open watches (below).
- `~/.claude/flowkit/parked.json`, when it exists: the panes the `handoff` tmux kit found parked
  at a start-up prompt (its README's "Start-up prompts"), by story. The kit rewrites the file
  every few seconds while a pane waits and removes it after, so the card goes back once the
  prompt is answered; a file older than five minutes is ignored.

Nothing is written outside the plugin's own store (the ticks).

## The test queue

What you can test now, and when the rest will be ready, kept by whoever runs the story (a
`coordinate` coordinator, or any session or script) beside the story file as
`~/.claude/stories/<story>.tests.md`. Plain text, one line per item, five fields between ` · `,
then any notes and the gate:

```
# my-story test queue
Login screen · !93 · iPhone 15 · ready to test · 14:30 · Gate: device test
Settings toggle · !94 · Pixel 8 · building · 15:10 · on the release build · Gate: merges on green
```

| Field | Holds |
|---|---|
| item | what to test, in a few words |
| MR | the merge request (`!93`, a URL), empty before one exists |
| device | where to test it (a phone, a simulator, a browser) |
| state | free words: `ready to test`, `building`, `waiting on review`, `tested` |
| ETA | a clock time (`14:30`) when it will be ready to test, empty once it is |
| notes | a sixth field and on: free words, kept as written |
| gate | `Gate: <text>`, a field of its own: what the item waits for between green and its merge (`merges on green`, `device test`, `your look`); a line without one has no gate |

Blank lines and `#` lines are skipped, a leading `- ` is dropped, and a missing field stays empty.
The gate is the first whole field after the item that starts `Gate:`, wherever it stands; a second
one is a note, and so is "held at the gate" inside a sentence. The writer (handoff's
`flowkit-capacity testq`, which reads the line the same way) rewrites the whole file whenever a
line changes and deletes it when the queue is empty. `/board` lists each line whole on the story's
card under TEST QUEUE; `/where` puts the five fields in columns under the story's row, with a gate
column of at most 14 characters when a line has one, and no line wider than 100 columns. A story
without the file shows nothing.

## Capacity

With the `handoff` capacity gate in use, `/where` prints a CAPACITY block above STORIES and the
Pane a CAPACITY line under its heading, both from `status.json`:

```
CAPACITY  slots 3/3 · queued 2 (next ~16:40) · simulator: demo-s2 since 14:10 (take)
          emulator free · backend: you, test hold until 15:30 · stalled: side-s1
```

Slots used of the machine's (`?` while `claude agents` did not answer), the queue with its first
launch's forecast start, each resource with who holds it, since when and why, and the sessions
shown as stalled. The board computes nothing: the file is written by `capacity.py` on every take,
release, launch and drain, and a file older than five minutes adds `as of HH:MM`. A `status.json`
of another version prints only that. Without the file there is no block.
`tests/fixtures/capacity-status.ts` is the shape both sides test against.

## Watches

A chain's last slice no longer waits for its pipeline: its close registers a watch with `handoff`
(`flowkit-watch`), a script polls the pipeline, and the close says `Next: watching <name>`. A watch
is no session, so nothing of it shows under SESSIONS; its name is open until the watch writes its
own section (`## <name> · …`: green, red, or what it reports). The card is **running** with
`watching <name>` while the last close says so, and while `status.json` lists an open watch of the
story whose section is not in yet: a story of two repositories is not done at the first green,
when the last section says `Next: none` and the second watch still runs. A red watch's section
starts a fix slice (`Next: started <name>`), which the card follows like any promised session.

`/where` lists the open watches under CAPACITY, one line each: the name, the story, the state
(`pending`, `fixing` with its fix slice, `waiting-base` with the watch it waits for, or the phase
after the pipeline: `merge`, `target`) and when it was armed:

```
WATCHES   app!12 · demo · pending · armed 14:10
          lib!9 · side · fixing (side-fix-1 started) · armed 13:20
```

`status.json` carries them as `watches` (version 3), rewritten whenever a watch opens, changes
state or goes.

## Surfaces

The Pane is drawn by the terminal (docked beside the transcript in fullscreen, else above the
prompt), the Desktop Code tab and the VS Code extension where they attach a surface. The plugin's
tests draw it on `terminal`, `desktop` and `vscode`, tick a step and read it back; they exercise
the hooks and the tree, not a surface's paint.

## Commands

| Command | Does |
|---|---|
| `/board` | opens the Pane |
| `/where [all]` | live sessions with context and cost, the usage windows, capacity and the open watches, the stories table; `all` adds the quiet ones |
| `/forme [story]` | the open FOR ME steps of every story touched in the last three days, or of one |

## Tests

`claude plugin test plugins/board` runs `tests/board.test.ts`.
