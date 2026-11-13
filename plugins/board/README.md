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
| **NEEDS YOU** | a session of the story waits on you (a question, a permission), or the last close left a launch pending (`Next: pending <name>`) or a free-text `Next:` |
| **STALLED** | no session of the story is working or waiting, and a session any close promised (`Next: started …`) never wrote its own close: it never came up, ended, or sits idle at its prompt. A new session gets 10 minutes to come up first; a stalled story nobody touches for seven days folds into the "more" line |
| **running** | a session of the story is working, or the promised one is still starting, or the last close queued its successor (`Next: queued <name>`: it waits for a slot, not for you, until a session of that name comes up; one that then ends without a close stalls the story) |
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
  (`started`, `spawned`, `pending` or `none`), the FOR ME block and any merge-request URL.
- `claude agents --json --all`: interactive sessions by `status`, background ones by `state`.
  Called only while the board is open.
- `~/.claude/session-stats/<session id>.json`, for the context and cost figures. The `handoff`
  status-line offer writes them; a session without the file shows without the numbers.

- `~/.claude/stories/<story>.tests.md`, when it exists: the story's test queue (below).
- `~/.claude/capacity/status.json`, when it exists: the [`handoff`](../handoff/README.md) capacity
  gate's snapshot, for the CAPACITY block (below).

Nothing is written outside the plugin's own store (the ticks).

## The test queue

What you can test now, and when the rest will be ready, kept by whoever runs the story (a
`coordinate` coordinator, or any session or script) beside the story file as
`~/.claude/stories/<story>.tests.md`. Plain text, one line per item, five fields between ` · `:

```
# my-story test queue
Login screen · !93 · iPhone 15 · ready to test · 14:30
Settings toggle · !94 · Pixel 8 · building · 15:10
```

| Field | Holds |
|---|---|
| item | what to test, in a few words |
| MR | the merge request (`!93`, a URL), empty before one exists |
| device | where to test it (a phone, a simulator, a browser) |
| state | free words: `ready to test`, `building`, `waiting on review`, `tested` |
| ETA | a clock time (`14:30`) when it will be ready to test, empty once it is |

Blank lines and `#` lines are skipped, a leading `- ` is dropped, and a missing field stays empty.
The writer rewrites the whole file whenever a line changes and deletes it when the queue is empty.
`/board` lists it on the story's card under TEST QUEUE, `/where` under the story's row; a story
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

## Surfaces

The Pane is drawn by the terminal (docked beside the transcript in fullscreen, else above the
prompt), the Desktop Code tab and the VS Code extension where they attach a surface. The plugin's
tests draw it on `terminal`, `desktop` and `vscode`, tick a step and read it back; they exercise
the hooks and the tree, not a surface's paint.

## Commands

| Command | Does |
|---|---|
| `/board` | opens the Pane |
| `/where [all]` | live sessions with context and cost, the usage windows, the stories table; `all` adds the quiet ones |
| `/forme [story]` | the open FOR ME steps of every story touched in the last three days, or of one |

## Tests

`claude plugin test plugins/board` runs `tests/board.test.ts`.
