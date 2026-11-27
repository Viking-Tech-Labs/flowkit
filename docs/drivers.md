# Launch drivers

A chain of sessions needs somewhere to run each session: a place the next session is started in,
found again later, and typed into. The `handoff` plugin calls what provides that a **launch
driver** and picks it with one line of `~/.config/flowkit/handoff.conf`:

```
driver=tmux          # the default: a tab per story, its sessions tiled panes
driver=background    # Claude Code's own background sessions (claude --bg), no terminal at all
driver=<name>        # an executable of your own, flowkit-driver-<name>
```

The first two are built in. The third is for everything else: another multiplexer, a terminal
with a remote-control command, a variation of your own. You write one executable that answers a
few requests; flowkit does the rest. `flowkit-driver-screen`, which ships with the plugin, is such
a driver for GNU screen and the example to copy.

**Experimental.** The requests below are version one, written against tmux, the background
service and GNU screen. Until a driver somebody else wrote passes `flowkit-driver test`, a release
may still change them; the handoff README's notes name any change. If you write one, an issue or
a discussion saying what did not fit is the most useful thing you can send.

## The executable

`driver=<name>` makes flowkit look for an executable named `flowkit-driver-<name>`: on the
`PATH`, then in `~/.local/bin`, then beside flowkit's own commands (where `flowkit-driver-screen`
is). Put yours in `~/.local/bin`: the tick's timer and the hooks do not all run with your shell's
`PATH`, and every flowkit command looks there. A name is letters, digits, `.`, `_` and `-`. With
no such executable a launch ends with exit 7 and says which file it looked for.

flowkit runs it as

```
flowkit-driver-<name> <request> [argument ...]
```

with nothing on its standard input, and gives it 10 seconds. It answers on its standard output and
exits:

| Exit | Means |
|---|---|
| 0 | answered |
| 2 | "I have no answer to this request": flowkit stops asking it and works without (see the table below) |
| anything else | it failed; the reason, one line, goes to standard error and reaches the user where there is a place for it |

Any language works; a shell script is enough. It keeps whatever state it needs itself. flowkit
remembers, in its own file (`~/.claude/flowkit/sessions.json`), which story each place was opened
for and which session runs in it, so a driver never has to.

## The requests

A **place** is whatever one session runs in: a window, a tab, a pane, a whole terminal. The driver
names each with an **id** of its own choosing, one word without a tab or a line break, which
flowkit hands back unchanged.

| Request | Arguments | Prints | Required |
|---|---|---|---|
| `open` | `<story> <name> <folder> <command>` | the new place's id, alone on a line | yes |
| `list` | none | every place, a line each: `<id>`, a tab, `<pid>` | yes |
| `type` | `<id> <text>` | nothing | yes |
| `screen` | `<id>` | what the place shows now, as text | no |
| `label` | `<id> <word>` | nothing | no |
| `close` | `<id>` | nothing | no |
| `pick` | `<id>` | nothing | no |

**`open <story> <name> <folder> <command>`** starts a new place and runs `<command>` in it, with
`<folder>` as its working directory. `<command>` is shell text (`claude --name '...' "$(cat
/path/to/prompt.md)"`), to be run by a shell: `sh -c "$4"` in the place, or typed into the
place's shell. `<story>` is the story's slug, or `adhoc` for a one-off session: group by it if
what you drive has groups (a tab per story, say), ignore it otherwise. `<name>` is the session's
name, good for a title. Return once the place exists; do not wait for claude. When the command
ends, the place should go and leave your `list`: that is how flowkit sees that claude exited at
its start (it then opens a place once more, and refuses the launch at the second exit). A driver
whose places outlive their command gets a warning after 15 seconds in place of that second try.

**`list`** prints every place the driver has that is still there, the ones it opened and, if it
can, the ones you opened by hand. The pid is the process the driver, or its program, started in
the place: a shell, or the command itself. flowkit finds the place a session runs in by walking up
from the session's process to one of these pids, so a session whose place has no pid in the list is
never typed into, and a session you started by hand is reached only if its place is listed.

**`type <id> <text>`** makes the characters of `<text>` reach what runs in the place as if typed,
control characters included, and nothing more: no line end added, nothing interpreted. flowkit
sends a line as its text, then half a second later a carriage return as a request of its own
(Claude Code reads an Enter that arrives with the text as part of a paste); Ctrl-U (byte 21)
before a line clears a half-typed one, and Escape (byte 27) ends a question.

**`screen <id>`** prints the rows the place shows now, top to bottom, as plain text; colour
sequences may stay in (flowkit reads dim text in the prompt box as a suggestion, not as something
typed). A few screens of history above the visible rows do no harm. Print a wrapped line as one
line if you can: a Remote Control link cut in two is read as the shorter link. The text has to be
what the place shows character for character, in UTF-8: flowkit looks for Claude Code's prompt
mark (`❯`) and the frame of its dialogs. A program that cannot write those should exit 2 here
rather than answer with something close: without a screen flowkit types without looking, but with
a screen that shows no prompt box it does not type at all.

**`label <id> <word>`** shows `<word>` beside the place where the user sees it (a title, a
border): `working`, `waiting` or `idle`, and an empty word to show none. It is asked for every
listed place at every tick, once a minute.

**`close <id>`** makes the place go, with whatever still runs in it.

**`pick <id>`** turns the user's terminal to the place.

## What flowkit does with them, and without

| Request | flowkit uses it for | Without it |
|---|---|---|
| `open` | every launch: `/handoff:close spawn`, `/handoff:spawn`, a queued launch, a watch's fix slice | nothing is launched |
| `list` | finding the place of a session (to type into it, to register it at its start), seeing that a launch came up or exited | no session is found where it runs |
| `type` | ringing a story's reporter at a close, continuing a session after an API error or a usage limit, the away line, `report now` | no line reaches a session |
| `screen` | the Remote Control link in a launch's `SPAWNED` line; naming the start-up prompt a launch is parked at; checking that the prompt box is free before a line is typed into it; the screen kept with a usage-limit stop | the link reads `pending`, a parked launch is reported without its prompt's first line, and a line is typed without a look at the prompt box (it can land in an open dialog or behind a half-typed draft) |
| `label` | the state beside each session | nothing beside a session says working, waiting or idle |
| `close` | `autoclose=on`: a finished slice's place, and a finished story's places | they stay until you close them |
| `pick` | `flowkit-tmux list`: a number jumps to that session | the list shows the sessions and cannot jump |

Everything else is flowkit's own and the same for every driver: the story file, the capacity gate
and the queue, the launch hold, the refusals before a launch (a running namesake, uncommitted
changes), the wait for the session to be listed by `claude agents`, the reporter, the watches.

What an outside driver does not get in this version, whatever it answers:

- `flowkit-spawn --pane` and `--new` are tmux's; they are ignored.
- The words on a story's tab (`running`, `needs you`, `done`) are tmux's: a driver is told a word
  per place, not per group.
- A launch's line is `SPAWNED driver=<name> id=<id> story=... name=... rc=... prompt=... created=1
  try=<1|2> [parked=<line>]`: the id is yours, there is no tab and no slot number.
- A queued launch and a watch's launch run your driver from a process that was not started in one
  of your places. If your driver needs to know where to open (a socket, a window), keep that
  yourself, in a file or an environment variable set for your whole login.
- The tmux kit (its key bindings and status bar) is tmux's.

## Trying a driver

```
flowkit-driver test <name>     # without a name: the driver handoff.conf names
```

`flowkit-driver` is on the `PATH` of a session's Bash tool; outside a session it is the file
`bin/flowkit-driver` of the installed `handoff` plugin. It asks each request with a harmless
program in place of claude: it opens one place under a story `flowkit-driver-test-<n>`, finds it
in the list, types two lines into it (one after an Escape), reads its screen (it has to show the
line and the prompt mark the program printed), labels it, turns
your terminal to it when run in one, and closes it. No Claude session is started and nothing of
yours is typed into. It prints a line per request:

```
open   passed
list   passed
type   passed
screen not answered · without it a launch's line has no Remote Control link, ...
label  passed
       (the other places show their words again at the next tick)
pick   not asked · no terminal here: run it in one to try it
close  passed
screen: passed
```

(The last line is the driver's name and the verdict; this is what `flowkit-driver test screen`
prints.) Labelling the test's place takes the words off the driver's other places until the next
tick writes them again, within a minute.

`passed`; `failed`, with what went wrong; `not answered`, with what flowkit does without it (the
driver exited 2); `not asked`. It exits 0 when every request passed or was left out by the driver,
1 when one failed or a required one was not answered, 2 when there is no such driver or nowhere to
try it. `flowkit-driver test tmux` runs the same against tmux (inside tmux, or with
`FLOWKIT_TMUX_SOCKET=<server>`), which is how the built-in driver is held to the same requests.

## The reference driver: GNU screen

`flowkit-driver-screen` is under a hundred lines of bash, written for the screen macOS ships (4.00.03).
That screen tells nobody outside a session which windows it has, so each place is a whole screen
session named `flowkit.<story>.<name>`, its one window running the command:

```
driver=screen                      # in ~/.config/flowkit/handoff.conf
screen -ls                         # the sessions
screen -r flowkit.billing.billing-s2   # open one; Ctrl-a d leaves it running
```

It answers `open`, `list`, `type`, `label` (the window's title) and `close`, and exits 2 for two
requests: `screen`, because this screen writes a copy of a window with every character cut to one
byte, which loses the prompt mark flowkit looks for; and `pick`, because screen cannot turn a
terminal that is attached elsewhere. So with `driver=screen` a launch's line says `rc=pending` (the
link is in the session, and in the Claude app), and a line is typed without a look at the prompt
box. Three things in it are worth copying: it refuses a request for a place that is not its own;
it addresses a place by an exact name (screen takes a bare name as a prefix of any); and it works
around its program's limits where they would lose data without a word (screen drops a typed
string of about 1000 bytes, so the text goes in pieces).

Copy it to `~/.local/bin/flowkit-driver-<yours>`, replace the `screen` calls, run
`flowkit-driver test <yours>`, then set `driver=<yours>`.
