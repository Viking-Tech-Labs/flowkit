# Extending flowkit

flowkit's plugins meet what is yours (a guard, a hook, a launcher, a script that runs at night) at
a few places. This page lists every one of them: what it is for, what you may read, write or call,
and what is promised to stay. The details of each are in the README of its plugin; the links say
where.

**Stable** below means: its names, arguments, output and exit codes change only in a minor release
whose README notes say so, and a field or a line that is added is no change. Everything this page
does not name is flowkit's own and may change in any release: the other files under
`~/.claude/flowkit/`, the functions of its scripts, the options it sets on tmux panes and windows,
the words of its warnings.

| Seam | You use it to | Promise |
|---|---|---|
| [The flag files](#the-flag-files) | tell every session that you are away, or out but reachable | stable |
| [The launch hold](#the-launch-hold) | make every launch wait while the machine is short of something | stable |
| [The story file's lines](#the-story-files-lines) | read what happened in a story, and add a line of your own | stable |
| [The sessions file](#the-sessions-file) | read where each session runs and which story it is filed under | stable to read, never to write |
| [`flowkit-session`](#flowkit-session-a-session-from-outside) | find a session, read its screen, type a line into it | stable |
| [The hand-off at a usage limit](#the-hand-off-at-a-usage-limit) | take a session over that a usage limit stopped | stable |
| [The board's JSON](#the-boards-json) | get the stories and their states as data | stable, versioned |
| [A launch driver](#a-launch-driver) | run sessions in another multiplexer or terminal than tmux | experimental |

No key of `~/.config/flowkit/handoff.conf` runs a command of yours at one of these moments, and
none will: a conf that starts programs is a second place to look when something ran. What your
setup adds goes in your own hook or shell function, which calls the commands below.

## Where the commands are

`flowkit-session`, `flowkit-away`, `flowkit-usage`, `flowkit-tmux` and `flowkit-tick` are on the
`PATH` of a session's Bash tool. A hook, a timer or a shell outside a session does not have that
`PATH`: `/handoff:setup-tmux` (and the timer's setup) copies them to `~/.local/bin`, and every
session start keeps the copies at the plugin's version. A copy finds the newest installed
`handoff` by itself, so nothing of yours names a version or a path into the plugin cache. Call
them by name with `~/.local/bin` on your `PATH`, or as `~/.local/bin/flowkit-session`.

## The flag files

`~/.claude/away` and `~/.claude/awake` (under `$CLAUDE_CONFIG_DIR` when that is set). A file that
is there and young enough is the flag; reading them is always safe.

| File | First line | Means | Counts |
|---|---|---|---|
| `away` | `YYYY-MM-DD HH:MM`, the minute it takes effect; then `by: <name>` and `set: <time>` | you are away: questions are denied, the recommended option is taken, marked "(auto)" | from that minute, for 12 hours after it or after the file's last write, whichever is later |
| `awake` | the time, then `by: <name>` | out but reachable: the computer stays up, questions are asked | for 12 hours from the file's last write |

Write them with `flowkit-away away|awake|back [--by <name>]`, which also tells the running
sessions; a file written raw (`date '+%Y-%m-%d %H:%M' > ~/.claude/away`) works too, in effect at
once, and removing a file clears its flag. `flowkit-away waiting` lists the sessions that wait on
you, a line each. Full text: the `handoff` README, "The flag files and `flowkit-away`: the seam".

## The launch hold

While `~/.claude/load-hold` exists, `flowkit-spawn` waits before anything else, looks again every
30 seconds, and launches once the file is gone. Two lines: an ISO time
(`2026-11-13T16:30:00+01:00`), then the reason. A guard of yours writes it (a memory watchdog, a
backup that must not be disturbed) and removes it; a hold longer than two hours rewrites the time,
since an older one is taken for stale and ignored. The launch says `> held <name> · <time> ·
<reason>` in the story file. Full text: the `handoff` README, "Starting the next session: spawn".

## The story file's lines

`~/.claude/stories/<slug>.md` is the record of a story: one `## <session> · <YYYY-MM-DD HH:MM>`
section per close (the `handoff` README, "What a close writes"), and between the sections one line
per event, each starting with `> `:

| Line | Written when |
|---|---|
| `> launched <name> · <time>` | `flowkit-spawn` started a session for the story |
| `> queued <name> · …`, `> launch failed <name> · …` | the capacity gate held a launch, or a queued one failed |
| `> held <name> · <time> · <reason>` | a launch waited in the launch hold |
| `> parked <name> · <time> · <first line>` | a launched session sits at a start-up prompt |
| `> retry <session> · <time> · <error> · <details>` | a turn ended on an API error or a usage limit |
| `> limit <session> · <time> · <what>` | what the usage-limit waiter does: `continues at …`, `not continued: …`, an account change, a claim |
| `> remote <session> · <time> · <what>` | Remote Control did not come back after an account change |
| `> watch <name> · … · <time>` | a watch opened, changed or ended |

Stable: the file's place, the section heading, the `Next:` line of a close, and for each line above
its first word and that a name follows it. The text after that is for people and may be reworded.

A line of your own: append `> <word> <session> · <YYYY-MM-DD HH:MM> · <text>` with a word that is
not in the table (your tool's name is a good one). flowkit reads past a line it does not know.
Append whole lines in one write, and only to a file that exists: a story's file is made by its
first launch or close.

## The sessions file

`~/.claude/flowkit/sessions.json` is what flowkit knows of each session and no terminal has to
keep for it:

```json
{"sessions": {"<session id>": {"backend": "tmux", "place": "%12", "story": "billing", "adhoc": false,
                               "pid": 4242, "at": 1795810056, "born": 1795810054}},
 "places": {"tmux %14": {"story": "billing", "adhoc": false, "born": 1795810100}}}
```

A session's entry is written at its start and kept for a day after its process went: `backend`
(`tmux`, `background`, or an outside driver's name) and `place` (the place's id there) say where it
runs, `story` which story it was started for (`""` outside one, and for a one-off, which has
`"adhoc": true`), `born` when its launch was typed. `places` holds what a launch wrote for a place
no session has registered in yet. Stable to read: those keys, with those meanings. Never write it:
flowkit replaces the file whole under a lock of its own, and a writer beside it loses entries. To
ask about one session, `flowkit-session locate` reads it for you and checks it against what runs
now.

## `flowkit-session`: a session from outside

New in `handoff` 0.17.0. Three things a hook of yours needs of a running session, whichever driver
it runs under (a tmux pane, a background session, a place of an outside driver), through the code
flowkit's own lines go through:

```text
flowkit-session locate <session> [--json]       where it runs, which story it was started for
flowkit-session read <session> [--rows <n>]     the rows its place shows now, as plain text
flowkit-session read <session> --box            one word: free, or what is in a typed line's way
flowkit-session type <session> <text> [--how free|line|over|escape|command]
                                                one line as its next prompt
```

`<session>` is a session id, a name as `claude agents` lists it, or a process id (digits alone).
A hook gets the first as `session_id` on its standard input and the last as `$CLAUDE_PID`.

**`locate`** prints `key: value` lines, or one JSON object with `--json`:

```text
session: 0a1b2c3d-0000-4000-8000-00000000000d
name: billing-s2
pid: 4242
kind: interactive
status: idle
cwd: /Users/me/repo
backend: tmux
place: %12
place_pid: 4100
copy: no
story: billing
```

`status` is Claude Code's own word, passed on from the session's file: `idle`, `busy`, `waiting`
or `shell` today, so test for the one you need. `place_pid` is the process the place itself started
(a pane's shell): when it is the parent of `pid`, the shell takes the keyboard back once the
session ends. `-` (JSON `null`) stands for what is not known. It exits 4, with the same lines and
no place, for a session that runs where flowkit cannot reach it.

**`type`** types only into a free prompt box unless told otherwise: on screen, empty, no dialog
below it, not in copy mode; a background session that is neither at work nor waiting on a prompt is
resumed with the text. It prints the place's id, or why nothing was typed (exit 5): `copy mode`,
`no prompt box`, `dialog`, `draft`, `waiting`, `busy`. `--how line` types without a look, `over`
clears a half-typed line first, `escape` sends Escape first (it ends a question's turn), `command`
is for a slash command. `read` and `type` take `--place <id>` in place of a session, for a place of
the configured driver with no session in it: a shell that should start one, say.

```sh
flowkit-session type --place %12 --how line "claude --resume 0a1b2c3d-0000-4000-8000-00000000000d"
```

Every command exits 0 when done, 1 when the driver's program did not answer (or the plugin is not
installed), 2 when called wrongly, 3 when no session answers to the word or more than one does, 4
when the session runs in no place flowkit reaches, and 5 when it was not done, with the reason as
the one line it prints. `flowkit-session <command> --help` has each command's output in full.
Stable: the commands, their arguments, the keys `locate` prints, the reason words and the exit
codes.

A line that a script types counts as typed by you unless it starts the way flowkit's own lines do.
Start yours with `wake: ` and flowkit reads it as a script's: it does not clear the away flag
(`idle_release=on`) and does not open the five minutes in which a session may still ask you.

## The hand-off at a usage limit

A turn that ends on a usage limit starts flowkit's waiter, which types a continue line into the
session once the limit has reset (the `handoff` README, "Usage limits"). A hook of yours may know
something better to do with that session: start it again under another login, move it to another
machine, tell somebody. Two of you typing into one session is the thing to avoid, so there is a
hand-off:

```text
flowkit-session claim <session> --by <name> [--for <minutes>] [--wait <seconds>]
flowkit-session release <session> --by <name>
```

`claim` takes the stop over: flowkit's waiter stands down, types nothing, and the story file says
`> limit <session> · <time> · <name> took it over until <time>: flowkit does not continue it
meanwhile`. The claim holds for `--for` minutes (15 without it, above 0 and 1440 at most; claim
again under the same name to hold it longer), only while the session runs in the same process, and
ends with the stop's record at the session's next clean turn end. `release` gives the session
back, and so does a claim that runs out: the waiter plans the stop again and continues the session
as it would have, with a line saying so. A claimer that dies therefore strands nothing.

Of a claim and the waiter's continue that come at the same moment exactly one wins. `claim` exits
0 and prints `claimed until <YYYY-MM-DD HH:MM>` only when nothing was typed and nothing will be;
it exits 5 with `flowkit continued it at <HH:MM>`, `flowkit is typing its continue` or `claimed by
<other> until <HH:MM>` when the session is not yours to take, and 3 when no usage-limit stop is
recorded for it. Your `StopFailure` hook runs beside flowkit's, which writes that record, so
`claim` waits up to `--wait` seconds for it (5 without it), and as long before it answers `flowkit
continued it` or `flowkit is typing its continue`: a continued session that stops again keeps the
record of the stop before until flowkit's hook has written the new one.

The shape of a hook that uses it (a `StopFailure` hook with the matcher `rate_limit`; keep the
hook itself short and do the work in a process it starts):

```sh
sid=$(jq -r .session_id)                                  # the hook's standard input
flowkit-session claim "$sid" --by mover --for 20 || exit 0    # not mine: flowkit carries on
if ! move_it_elsewhere "$sid"; then                       # yours: where it goes, and how
  flowkit-session release "$sid" --by mover               # changed my mind: flowkit continues it
fi
```

A session whose process ended (you stopped it to start it elsewhere) needs no release: its waiter
ends with the process. Sessions that started before `handoff` 0.17.0 was installed still run the
older waiter, which knows no claim, until they are restarted. Stable: the two commands, their
output lines and exit codes, and the `> limit` line's first word. The record a stop leaves
(`~/.claude/flowkit/retry/<session id>.json`) is flowkit's own: read it if you must, never write
it.

## The board's JSON

`flowkit-board snapshot` prints what the board knows as one JSON object: the stories with their
states (`needs-you`, `stalled`, `running`, `done`), the sessions of each, the FOR ME steps, and
the live sessions of no story. Its `version` field is raised when a field is renamed or dropped.
The format is the promise, the code behind it is not: the `board` README, "`flowkit-board
snapshot`: the stories as JSON". It needs `bun`, or Node 22.18 or newer.

## A launch driver

`driver=<name>` in `handoff.conf` starts every session through an executable of your own,
`flowkit-driver-<name>`, which answers three requests (`open`, `list`, `type`) and up to four more.
[`drivers.md`](drivers.md) is its contract, and `flowkit-driver test <name>` tries one request by
request. It is experimental until a driver written outside this repository passes: until then a
release may still change the requests, and the `handoff` README's notes name any change.
`flowkit-session` works over an outside driver as it does over tmux.

## More places that take something of yours

Each is described where it lives, and each takes a value or a file, never code inside flowkit:

- your own model ladder, `~/.config/flowkit/ladder.md`: the `handoff` README, "Your own ladder";
- a step of your own in every close: the `handoff` README, "Close step";
- a watch on anything a command can check, and a command run when it turns green
  (`flowkit-watch add --check …`, `--on-green-run …`): the `handoff` README, "Watches";
- machine resources that only one session may use at a time (a device, a local stack): the
  `handoff` README, "Capacity";
- the plan's usage as data, `flowkit-usage --json`: the `handoff` README, "The flag files and
  `flowkit-away`: the seam".
