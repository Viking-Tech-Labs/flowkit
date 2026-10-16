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
| **running** | a session of the story is working, or the promised one is still starting |
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

Nothing is written outside the plugin's own store (the ticks).

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
