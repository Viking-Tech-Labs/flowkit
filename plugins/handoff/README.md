# handoff

Close a Claude Code session so the next one starts cold, with nothing lost and nothing worked out
twice: green work committed, stale docs fixed, a pick-up block written into the repo's plan doc,
the next session's prompt drafted when repo work is left, and the close recorded in the story's
file.

Install, after adding the flowkit marketplace (see the root README): `claude plugin install handoff@flowkit`.

Skills are always typed with the plugin's name in front (`/handoff:close`), so a skill of your own
called `handoff` keeps working beside this one.

| Skill | What it does |
|---|---|
| `/handoff:close [modes] [what next]` | Closes the session: commits green work (never pushes), fixes docs it made stale, writes the pick-up block, drafts the next session's prompt when repo work is left, closes with `DONE` / `FOR ME` / `OPEN` / `LEFT` and records the close in the story file. |
| `/handoff:away` | Marks you away (`~/.claude/away`, in effect five minutes later, cleared by `/handoff:back` or 12 hours after): the idle coordinators get a last call at once; then every session, running already or started later, stops asking and takes the recommended option, marked "(auto)", unless you typed to it in the last five minutes; a session already sitting on a question is told so (below). |
| `/handoff:awake` | Marks you out but reachable from a phone (`~/.claude/awake`, for 12 hours or until `/handoff:back`): the computer is kept awake between turns as while away, and sessions still ask you. It clears an away flag. |
| `/handoff:back` | Clears both flags, so sessions ask you again, and lists the sessions waiting on you right now. |
| `/handoff:spawn [single] <prompt>` | Starts any session with a prompt the way a close's `spawn` does: in its story's tab, or with `single` as a one-off in a shared `adhoc` tab ("open a fresh session in that repo"); with `report_to=on` the new session reports its verdict back here (below). |
| `/handoff:setup` | Offers the status line below, as a diff of your settings, and writes it only on your yes; asks which launch driver spawned sessions use, and for background sessions offers two settings of yours; offers the `handoff.conf` keys below (tabs closing on their own, continuing after an API error, clearing away at the keyboard, where a chain ends, a helper reporting back); on macOS offers the timer that runs the tick (see "The tick"). `/handoff:setup remove` takes away what the setups installed. |
| `/handoff:setup-tmux` | Offers the tmux kit below and the one line that loads it from your tmux.conf, and writes only on your yes; explains the launch settings. |

Modes, combined freely: `quick` skips the docs sweep; `prompt-only` prints only the next prompt
(or the one line saying what remains); `overnight` runs unattended and sets the away flag; `spawn`
saves the prompt and starts the next session in the story's tmux tab, or in the background (below). Any other text is what the next
session should do, passed into its prompt as written.

## Starting the next session: spawn

`/handoff:close spawn` saves the next prompt and starts that session through `flowkit-spawn`
(`bin/`, on the PATH of Claude's Bash tool), with Remote Control on so you can follow it from
your phone. Two drivers are built in and set once with `/handoff:setup`; a third kind is an
executable of your own (see "An outside driver" below). The tmux driver:

- each story gets its own tab, named after the story; its sessions are tiled in launch order, up
  to 9, then a second tab `<story>-2`;
- it refuses when a session of that name already runs or the repo has uncommitted changes, since
  two sessions on one working tree overwrite each other's work (an untracked `.claude/`, where
  EnterWorktree keeps a session's worktrees, does not count);
- it waits until `claude agents` lists the new session and prints its Remote Control link; a
  session that does not come up in 15 seconds is most often waiting at a folder-trust prompt,
  and a pane that sits at one is reported with the prompt's first line (see "Start-up prompts");
- a claude that exits at start-up (a bad flag, a broken install: within those 15 seconds the
  pane is a bare shell again and nothing is listed) gets the launch typed once more into the
  same pane, after the same refusals, with `warning: session '<name>' exited at start-up in
  <pane>; typed again` on stderr and `try=2` in the `SPAWNED` line; a second exit ends with exit
  10, `session '<name>' exited twice at start-up in pane <id>; its output is in the pane`, no
  session started, and the close records the prompt as pending with that pane under FOR ME;
- with `reporter=on`, a launch that finds no `<story>-report` running starts one first, so a
  story whose reporter died gets a new one at its next launch: in slot 1 of a new story tab,
  else in a pane split after the last one of the session's window (slot 2 of a one-pane window
  handed over with `--pane`). Each close's record says `reporter: rung`, `waiting` or `none`.

Every driver first honours a **launch hold**: while `~/.claude/load-hold` exists, `flowkit-spawn`
waits before anything else, checking every 30 seconds, and launches once the file is gone. Any
guard of your own can write it (a memory watchdog, say): two lines, an ISO time (`2026-11-13T16:30:00+01:00`)
then the reason. The launch says once, on stderr and as `> held <name> · <time> · <reason>` in the
story file, that it is held and why, and `launch hold lifted after <n> min` on stderr when it goes.
A flag whose time is more than 2 hours old is stale: it is ignored, with a line saying so (a guard
that holds longer rewrites the time). `FLOWKIT_IGNORE_HOLD=1` skips the wait; `--dry-run` only
says the launch would wait. A close or `/handoff:spawn` that finds the file runs the launch as a
background task, since the wait can outlast the Bash tool's timeout.

Outside tmux nothing launches: the close prints the launch line under FOR ME and records the
prompt as pending. After a launch the closing session leaves the repo alone.

Whichever copy of `flowkit-spawn` is called, the installed release answers: a session keeps the
plugin directory it started with, and an older cache directory can sit before the newest on the
PATH, so a copy older than the one `installed_plugins.json` names hands over to that one
(`--dry-run` says which copy answered). A close in a session started before a release spawns
with the newest release anyway; only the skill text waits for a restart.

New in 0.10.2: a pane `flowkit-spawn` creates no longer starts with the caller's plugin
directories on its PATH. tmux gives a pane a script creates the script's PATH, so a session
spawned by a session kept that one's `bin/` directories ahead of its own, release after release,
and the other commands of an older release answered there (`command -v flowkit-capacity` naming
an old version is the sign). A pane created before 0.10.2 keeps that PATH until it is closed.

The background driver (`driver=background`) starts the session with `claude --bg`, Claude's own
background sessions, so no tmux is needed (and one account only, see "Two logins on one config
folder"):

- the session shows in `claude agents`, in the board and on your phone (Remote Control);
  `claude attach <name>` opens it in a terminal;
- the same refusals as tmux (a running namesake, uncommitted changes), plus one of its own: a
  folder you never opened in Claude refuses to start until you run `claude` there once and
  accept the trust prompt;
- with `reporter=on` the story's first launch also starts `<story>-report` in the background.
  Each close rings it: `handoff.py record` wakes it with `closed <session>` as its prompt
  (stopping it first when it sits idle, since a running background session cannot be typed into);
- a background session idle at its prompt for some hours (8 h on Claude Code 2.1.301; the
  number changes between versions) is stopped by Claude, its conversation kept: the next ring wakes a stopped reporter, and `claude attach` reopens any;
- `claude --bg` does not pass on your shell's environment; the next session gets
  `XDG_CONFIG_HOME` from the one that started it, so both read the same settings.

An outside driver (`driver=<name>`, new in 0.16.0 and experimental) starts the session through an
executable of your own, `flowkit-driver-<name>`, for another multiplexer or terminal than tmux:

- the executable answers three requests (open a place for a story's session, list its places, type
  into one) and up to four more (show a place's screen, label it, close it, turn your terminal to
  it); [`docs/drivers.md`](../../docs/drivers.md) has them, and what flowkit does without each
  optional one;
- everything above that is flowkit's and the same as with tmux: the refusals, the reporter, the
  wait until `claude agents` lists the session, one more try when claude exits at start-up (a place
  is opened once more; exit 10 at the second exit), the story file, the gate. The launch's line is
  `SPAWNED driver=<name> id=<the place, in the driver's words> …`;
- the sessions in the driver's places are then the ones a close rings, the tick labels, continues
  after an API error or a usage limit and, with `autoclose=on`, closes; the tmux kit is not used;
- `flowkit-driver test <name>` tries a driver with a harmless program in place of claude and says,
  request by request, what passed, what failed and what the driver leaves out;
- `flowkit-driver-screen` ships as the reference: `driver=screen` runs each session in a GNU screen
  session of its own (`screen -ls` lists them, `screen -r <id>` opens one). The screen macOS ships
  cannot show flowkit a window's text faithfully, so that driver leaves the screen out: a launch's
  line says `rc=pending` and a line is typed without a look at the prompt box;
- a name with no executable ends a launch with exit 7 and the file it looked for. `/handoff:setup`
  offers the two built-in drivers; write this line into the file yourself.

The settings are plain `key=value` lines in `~/.config/flowkit/handoff.conf`, outside the plugin
so an update or an uninstall keeps them, and readable by the scripts that run outside Claude:

| Key | Default | Means |
|---|---|---|
| `driver` | `tmux` | How the next session starts: `tmux` (a tab per story), `background` (`claude --bg`), or the name of an outside driver, an executable `flowkit-driver-<name>` (`screen` ships with the plugin; [`docs/drivers.md`](../../docs/drivers.md)). |
| `reporter` | `off` | `on` starts a `<story>-report` session whenever a story's launch finds none running (in the background with that driver): it collects the story's closes and writes one final report, with one merged FOR ME list, when the story is done; the story's `Chain end:` line is the yardstick of its LEFT and of the report's first line. Each close rings it: `handoff.py record` types `closed <session>` into its pane, or wakes the background one with it. |
| `permission_mode` | empty | The `--permission-mode` of spawned sessions; empty keeps your default. A chain that runs while you are away needs a mode that does not stop to ask. |
| `autoclose` | `off` | `on` lets the tick close a finished story's tab on its own (see "The tmux kit"). `/handoff:setup` offers it. |
| `autoretry` | `off` | `on` lets a session an API error stopped carry on by itself while you are away (see "API errors"). `/handoff:setup` offers it. |
| `limitresume` | `on` | `off` leaves a session that a usage limit stopped where it is; by default it is continued after the limit resets (see "Usage limits"). |
| `chrome` | empty | `on` never passes `--no-chrome` to a spawned session. Empty passes it only while Claude Code's Chrome question has never been answered on this machine (see "Start-up prompts"). |
| `remind` | `off` | `on` lets the tick push what waits on you while you are neither away nor at the computer (see "Reminders"). `/handoff:setup` offers it. |
| `notify` | empty | A shell command that gets a reminder's line on its stdin when no reporter could push it (see "Reminders"); empty falls back to a macOS notification. |
| `chain_end` | `mr-ready` | What a story's chain ends with, proposed when its first prompt is drafted (see "Where a chain ends"): `branch`, `draft-mr`, `mr-ready` or `merge`. `/handoff:setup` offers it. |
| `report_to` | `off` | `on` writes `Report-to: <the launching session>` into the brief of a helper (`/handoff:spawn` with a prompt, or a close's review or investigation beside the chain), so its close sends the verdict back (see "A helper reports back"); a name sends every helper's verdict to that session. `/handoff:setup` offers it. |

The model and effort of the next session come from its prompt (see "Your own ladder"), passed as
they are.

## The tmux kit

`/handoff:setup-tmux` copies a small tmux setup to `~/.config/flowkit/tmux.conf` (and
`flowkit-away`, `flowkit-usage` and `flowkit-session`, see "Being away" and "Sessions from outside") and adds one line to your tmux.conf: `source-file
~/.config/flowkit/tmux.conf`. It keeps tmux's Ctrl-b prefix
and adds: mouse on, tabs and panes counted from 1, **⚑ n need you** in the status bar with ⚑n on
each tab that has sessions waiting on you, a one-word state after each story or adhoc tab's
name (`3:my-story · running`), `prefix a` to list every Claude session and jump to one, and `prefix H` for the one-page cheat sheet (`tmux/cheatsheet.md`). The conf calls the
helpers in `~/.local/bin/` (`flowkit-tick` for the status bar, `flowkit-tmux` for the keys), copies kept at the plugin's version by a session-start
hook, because the plugin's own directory moves with every version; the hook does nothing until
you install the kit or the timer. Since 0.8.1 the copy only goes forward (a session on an older plugin leaves a newer kit alone; `~/.config/flowkit/kit-version` names the version it came from) and finds the plugin's scripts through the newest handoff in `installed_plugins.json`; `~/.local/scripts/capacity.py` does the same for a 0.8.0 tick, whose drain looked there. The status bar prints the line the tick kept (see "The tick"), since tmux reruns a `#()` job at
every redraw that falls in a new second, not every 15 seconds. Put your own changes below the `source-file` line: a setting there wins.
Sessions started in the background have no pane and are not counted; `prefix a` lists them.

The tab's word is its most urgent session's state, in this order: **needs you** (a question or a
permission, or a pane left at a start-up prompt, see "Start-up prompts"), **error** (the last turn
failed on an API error), **limit** (it stopped on a usage
limit and waits for the reset, see "Usage limits"), **stalled** (idle 15 minutes or
more, transcript and forks untouched, no close recorded), **running**, **watching** (closed, but a
watch or background task still runs; also a story with an open `flowkit-watch` and nothing more
urgent in its tab, see "Watches"), **done** (closed and idle). A close counts once the story
file has its `Session:` line. Your own windows, without a story or adhoc mark, get no word.

New in 0.5.0, off until you turn it on: with `autoclose=on` in `handoff.conf`, the same tick
closes a story's tab once the story is finished, so finished tabs no longer pile up. A story is
finished when its reporter's final report is in, or, with no reporter left, when every session a
close launched has recorded its own close (a session started after the final report needs its
own close either way); while the closes are still missing, the tick types
`report now` into an idle reporter (at most every 5 minutes) so it writes that report, though never
while a watch of the story is open: its own section rings the reporter when the pipeline ends. The whole
story goes at once, every pane in every one of its tabs, the reporter last, and only when nothing
in it is busy, waiting on you, watching or in error, no pane runs anything but Claude or a bare
shell, the story file has sat 5 minutes and every session's transcript and forks have been still
for 5 minutes. A tab whose sessions all exited closes too, but a tab `flowkit-spawn` opened, or
a pane it typed a launch into, less than 2 minutes ago is left alone, and the tick looks again
right before it kills (it lists the panes once and decides a `claude agents` later). `tmux
set-option -w @flowkit_hold 1` in a tab keeps its story open; the adhoc tab and your own windows
are never closed, except a one-pane window of your own that you hand `flowkit-spawn --pane`: that
becomes the story's next tab and goes with the story. A reporter started in a window of your own
(a tab with other panes in it, handed over with `--pane`) stays with that window.

With `autoclose=on` the tick also closes a finished slice's pane before the story ends, so a long
chain's tab does not fill with idle sessions: once the slice's close is recorded in the story file
and a session its `Next: started` line names is running (in a pane or in the background), or a
watch its `Next: watching` line names is open. Never
the reporter, a coordinator (a name on a `~/.claude/coordination/*.md` board's `Coordinator:` line,
or one with `coord` at the start or after a `-`), a session in the adhoc tab or a window with no
story mark, a held tab, or a session that is busy, waits on you, is in error or whose transcript
moved in the last 5 minutes. `flowkit-tmux closable` is a dry run: it prints the panes the tick
would close and closes nothing.

## The tick

Some of the plugin's work happens while nobody types, in a pass every 15 seconds that

- drains the capacity queue and stops a resource left free ("Capacity");
- starts again a watch poller or a usage-limit waiter whose process went ("Watches", "Usage
  limits");
- brings Remote Control back after the account of a login changed ("Two logins on one config
  folder");
- sends the reminders, with `remind=on` ("Reminders");
- in tmux, marks the panes and tabs and, with `autoclose=on`, closes what is finished ("The tmux
  kit").

That pass is `flowkit-tick`, and each job in it runs on its own: one that fails, or whose program
is not installed, stops no other. Two things can run it:

- **A timer, on macOS**, new in 0.14.0. `/handoff:setup` offers it: a LaunchAgent,
  `~/Library/LaunchAgents/flowkit.tick.plist`, that runs `~/.local/bin/flowkit-tick --timer` every
  15 seconds from login on, with tmux attached, detached or not installed at all, so a chain on
  the `background` driver gets its queue drained and its reminders too. launchd starts a job with
  almost no environment, so the setup writes yours into the file: the `PATH` it ran with (your
  `claude`, `tmux` and code host's command are found through it) and `CLAUDE_CONFIG_DIR`,
  `XDG_CONFIG_HOME` and `LANG` where your shell sets them. The timer ticks the one config folder
  the setup ran in; run the setup again after you change any of those and it shows the difference.
  Two limits of a job launchd starts. It has none of your shell's other variables: a token your
  code host's command reads from the environment has to be in that command's own config. And it
  is not your terminal: macOS may ask once, for `python3`, before the tick's work reads a
  repository under Desktop, Documents or Downloads.
- **The tmux status bar, anywhere.** Where no timed pass ran in the last minute (Linux, or macOS
  without the timer), the kit's status-bar job runs the pass itself, at most every 10 seconds, as
  it did before 0.14.0. It stops while no tmux client is attached.

With a timer the status bar only prints the line the last pass kept. `flowkit-tick status` says
who keeps the tick, when its last pass ran and what failed in it, and exits 1 when none ran in the
last minute.

The tick's lock and what it keeps are `~/.claude/flowkit/tick.lock` and `tick.json`, and the hooks
keep each session's state (the context steps it was told, when you last typed to it) in
`~/.claude/flowkit/context/<session id>.json`, dropped 30 days after its last prompt. Before
0.14.0 all of these were in the temp folder, which a timer and a terminal do not always share.
What an older version left there is ignored, except that a session started before the update is
still read from there until it is resumed. A tmux.conf the kit wrote before 0.14.0 runs
`flowkit-tmux waiting` in its status bar, which hands over to `flowkit-tick bar`: its line and its
tick go on, and it needs no change.

New in 0.15.0: what flowkit knows of a session is in one file, `~/.claude/flowkit/sessions.json`,
whatever the session runs in. A session registers itself there at its start (the SessionStart
hook, at a resume too): the tmux pane it runs in and the story that pane's tab was opened for. The
tick adds when it last rang a story's reporter and, by pane, when it first saw a start-up prompt.
The tab words, `autoclose` and the story a session's lines are filed under are read from that
file, not from tmux. What tmux still holds is what it shows (the word and the count on a tab, the
state in a pane's border), your own switch (`@flowkit_hold`), and what `flowkit-spawn` marks as it
launches: the tab's story, read once by the session as it registers and for a pane with no
session in it, and the stamp of a launch just typed. A session that started before 0.15.0
registered nothing: it is read from its tab as before until it starts again, and what an older
tick kept in a pane is still honoured; both stop with 0.17.0 (0.16.0 came a day after this one
and still reads them). The file is flowkit's own: read it
if you like, write it through nothing but a session's start.

New in 0.15.1: a launch writes that file too. Before `flowkit-spawn` types a launch into a pane it
writes, under the pane, the story the pane was opened for and when the launch was typed; the
session takes both over as it registers, so the tab words, `autoclose` and the two minutes a
starting pane is left alone no longer depend on what the tab is marked with. A launch whose
session never comes up leaves its record, and the pane still counts as the story's; a pane used
again is the new launch's. A background session's story is written under its session id once
Claude Code lists it. The tab keeps its mark as its place in the layout (which tab is a story's
first, second, third: the launch finds a free slot through it), and a pane keeps the stamp a tick
of 0.15.0 reads. A tab opened by 0.15.0 or older has no record in the file: it is read from its
mark as before, until 0.17.0. `flowkit-spawn` itself reaches tmux and `claude --bg` only through
`scripts/backend.py` now; its options, its lines and its exit codes are what they were. A queued
launch and a watch keep where to launch as one word of the launch driver's own (`--target`,
`<server>/<session>` for tmux) in place of `--tmux-socket` and `--tmux-session`; both options
stay, and a launch queued or a watch added by 0.15.0 still starts with them.

`~/.local/bin/flowkit-tick` is a copy with no version in its path, like `flowkit-tmux`,
`flowkit-away`, `flowkit-usage` and `flowkit-session` beside it: moved forward at every session start once the timer
or the kit is installed, and finding the plugin's scripts through the newest handoff in
`installed_plugins.json`.

**Taking it away.** `/handoff:setup remove` (`handoff.py setup remove`; `--dry-run` shows it
first) unloads and deletes the timer and deletes the four commands in `~/.local/bin`, the kit's
files in `~/.config/flowkit/` and the `source-file` line in your tmux.conf (a backup beside it).
Your settings stay: `handoff.conf`, `capacity.conf`, a ladder, the status line. Do it before you
uninstall the plugin: a timer left behind runs a tick that finds no plugin. The timer alone, by
hand: `launchctl bootout gui/$(id -u)/flowkit.tick; rm ~/Library/LaunchAgents/flowkit.tick.plist`.

## Being away

New in 0.10.0: the flag takes effect five minutes after it is set, with a last call to the
coordinators first; a session you typed to in the last five minutes may still ask; every session
hears who set and who cleared it.

New in 0.10.1: the nudge at the minute the flag takes effect comes from a process `away` starts,
so it runs without the tmux tick too, and it leaves a session you are answering until five minutes
after your last prompt to it; the ladder plugin's `max` gate and `autoretry` count the flag from
the moment it is set; a coordinator's wake of a stopped worker no longer counts as you typing;
`away --by` takes a session's title, spaces and all.

`/handoff:away` runs `handoff.py away --by <this session>`, the one command that sets the flag.
It writes `~/.claude/away`:

```text
2026-11-19 23:15
by: demo-coord
set: 2026-11-19 23:10
```

The first line is the stamp the flag takes effect, five minutes after it was set; the flag counts
for 12 hours from then, and every unattended close re-stamps it with `handoff.py restamp` while it
exists (the file's time moves, the text stays), so a chain that runs longer than that stays
unattended until `/handoff:back`. A flag written raw, its stamp alone on one line, still works: it
is in effect from its stamp, set by `unknown`. Running `away` while a flag is set leaves it as it
is. `handoff.py away-status` prints `away: since <stamp>` (in effect), `away: from <stamp> (set by
<name> at <time>)` (not yet) or `away: off`. Whatever asks whether you can still be asked means in
effect: the question gate, the closes' "unattended", the coordinators' "ask nothing" and the
reminders' silence. What only needs to know that the night has begun counts the flag from the
moment it is written, the five minutes included: the keep-awake hook (the computer stays up for
the nudge at the effective time), the ladder plugin's `max` gate and `autoretry`, since
`/handoff:close overnight spawn` launches within those minutes and an API error can end a turn in
them.

- **A last call first.** `away` types one line into every idle coordinator at once (`last call:
  the user goes away at 23:15. Ask now anything you need from them; …`), so a coordinator that
  still needs a decision asks it while you are there. A coordinator is a name on a
  `~/.claude/coordination/*.md` board's `Coordinator:` line, or one with `coord` at the start or
  after a `-`. The line is typed as the doorbells type, only into an idle session in a tmux pane
  whose prompt box is free (no draft, no dialog, not in copy mode); never into a reporter, the
  session that set the flag, or a session that is busy or waits. One line per coordinator comes
  back: `last call <name>`, or `no last call <name>: <why>`. A draft is what you typed or pasted:
  the dim text Claude Code draws in an empty box (its prompt suggestion, a new session's `Try …`)
  is none, here and wherever flowkit types into a pane (new in 0.10.2).
- **Then no session asks you anything.** A PreToolUse hook on AskUserQuestion (`hooks/hooks.json`)
  denies the question while the flag is in effect, with a reason the session acts on: take the
  recommended option, mark it "(auto)" in its decision log and its close, carry on, never end a
  turn on a question. The harness enforces it in every session that loads the plugin, hand-started
  or spawned, in a pane or in the background, so nothing depends on a session remembering to read
  the flag. One exception keeps a conversation going: a session you typed a prompt to, or whose
  question you answered, in the last five minutes may still ask, at the computer or from the
  phone. Typed by you means not a task's notification, another session's message, a `/command`'s
  or a `!` line's wrapper, a line flowkit types (the doorbells, the limit and error continues, the
  away nudge, the last call, a `/remote-control`, a coordinator's `wake: ` of a stopped worker),
  nor the session's first prompt, which is its launch brief. The context hook keeps that stamp per
  session (UserPromptSubmit, and PostToolUse on AskUserQuestion).
- **Every session hears of it.** At its next prompt or batch of tool calls, each session gets one
  line when the flag is set (who set it, when, and when it takes effect) and one when it is
  cleared (by whom and when, or that it expired or was removed by hand).
- **A session already sitting on a question is nudged** once the flag takes effect: `away` starts
  a process of its own (`handoff.py away-nudge --wait`) that waits for that minute and then runs
  `bin/flowkit-away nudge`, once per flag. It reads `claude agents --json`; each session with
  `status: waiting` on `input needed` gets the away line: in a tmux pane, Escape ends the
  question's turn and the line is typed as its next prompt (as the reporter doorbell types); a
  background session is stopped and resumed with the line as its prompt. One session is left
  alone: the one you typed to, or whose question you answered, in the last five minutes, since
  you may be answering it right now; the process comes back once a minute and nudges it when
  those five minutes are over. The nudge needs no tmux: with `driver=background`, without the kit
  or with no client attached it runs the same, and a background session parked on a question is
  resumed with the line; only a question in a plain terminal outside tmux cannot be reached, and
  waits for you. A flag written raw starts no such process: a shell function that writes one
  calls `flowkit-away nudge` itself. `flowkit-away nudge` sorts the sessions as `nudged`,
  `permission` (a permission prompt is never answered, since nothing here approves on your
  behalf: answer it from the phone), `unreachable` (a question in a plain terminal outside tmux),
  `skipped` (a `<story>-report`, which never asks). Idle and busy sessions are left alone, since
  the hook catches their next question; a session that ended its turn on a question in prose
  stays as it is.

`/handoff:back` runs `handoff.py back --by <this session>`: the away flag and the awake flag go,
every session hears who cleared the away flag, and the sessions waiting on you are listed.

### Out but reachable: awake

New in 0.12.0. `/handoff:awake` runs `handoff.py awake --by <this session>` and writes
`~/.claude/awake` (the time, then `by: <name>`). It is for going out with the phone: sessions keep
asking you, over Remote Control, and a launch at effort `max` still asks, but the computer is
kept awake between turns as while away (see "Keeping the computer awake"), and with
`autoretry=on` a session an API error stopped carries on (see "API errors"). The flag counts for
12 hours from its write; setting it again starts them again; `/handoff:back` clears it. The two
flags are one or the other: `awake` clears an away flag (every session hears it), and `back`
clears both.

### Back at the keyboard: `idle_release`

New in 0.12.0, off by default. With `idle_release=on` in `handoff.conf`, a prompt you type at the
computer clears an away flag that has been in effect for 30 minutes or more, so a flag left behind
by a day out does not keep the sessions silent all evening. "At the computer" is read from the
keyboard and the mouse: one of them was used in the last two minutes (macOS's HID idle time), so
a prompt sent from the phone clears nothing, and neither does anything nobody typed (a task's
notice, another session's message, a line flowkit types, a session's launch brief). The first 30
minutes belong to the goodnight prompts. Every session hears `Away flag cleared by a prompt typed
at the computer`. The awake flag is left as it is. `handoff.py conf idle_release on` refuses where
no `ioreg` answers (anything but macOS); a key set by hand there does nothing.

### The flag files and `flowkit-away`: the seam

Two files are the whole interface, for a shell function, a launcher or a hook of your own:

| File | Written by | Means | Counts |
|---|---|---|---|
| `~/.claude/away` | `handoff.py away` (the stamp it takes effect, `by:`, `set:`) | you are away: no questions, "(auto)" | from its stamp, for 12 hours after the stamp or the file's last write, whichever is later |
| `~/.claude/awake` | `handoff.py awake` (the time, `by:`) | out but reachable: the computer stays up, questions are asked | for 12 hours from the file's last write |

Both sit under `$CLAUDE_CONFIG_DIR` when that is set. Reading them is always safe: a file that is
there and young enough is the flag. Writing them is better left to the commands, which also tell
the sessions: `/handoff:setup-tmux` copies `flowkit-away` to `~/.local/bin` beside `flowkit-tmux`,
kept at the plugin's version at every session start, so a shell function of your own calls

```text
flowkit-away away [--by <name>]     handoff.py away: the flag, the last call, the nudge at its minute
flowkit-away awake [--by <name>]    handoff.py awake
flowkit-away back [--by <name>]     handoff.py back: both flags cleared, who waits on you
flowkit-away nudge [--dry-run] [--only <name>]...
flowkit-away waiting
```

`--by` is the name the sessions hear (`shell` without it); the copy finds the newest installed
`handoff` by itself, so nothing of yours names a version or a path into the plugin cache. A flag
written raw still works (`date '+%Y-%m-%d %H:%M' > ~/.claude/away`: in effect at once, set by
`unknown`, no last call and no nudge; the same line into `~/.claude/awake` for the other), and
removing a file clears its flag. `handoff.conf` holds no command to run at `away` or `back`: what
your own setup adds to them (a power setting, a hold of your own) goes in the shell function that
calls `flowkit-away`. The flag is the record and the nudge a courtesy: a session the nudge could
not reach still reads the flag at its next question.

`/handoff:away` (and so `/handoff:close overnight`) also prints how much of your plan is left, so
you see before you leave whether the night fits:

```text
Week 49% used, resets Tue 02:59 (in 15 h) · Fable 37% of its bucket, 19% of the week (cap 50%) · 5-hour 9%
```

New in 0.6.0, the same line from `handoff.py usage` (`--json` for the fields), or `flowkit-usage`,
which `/handoff:setup-tmux` copies to `~/.local/bin`. Claude Code answers it itself (a `get_usage`
request through `claude -p`, no model call, no transcript, about 5 s), kept 10 minutes per
account (`--json` names it, see "Two logins on one config folder"). A failed call says so and
falls back to the newest `~/.claude/usage-stats/week-*.json`, marked old: the status line of
`/handoff:setup` writes one per week and login since 0.13.0 (see "The status line"), and a week of
another login is passed over; a file that names no login, as a status line of your own may write,
still counts.

## API errors

New in 0.7.0. When an API error ends a turn, Claude Code fires `StopFailure` instead of `Stop`, and
the session sits there until something prompts it. A hook on it (`handoff.py api-error`) always:

- keeps the computer awake between turns, as `Stop` would have (see "Keeping the computer awake")
- writes `~/.claude/flowkit/retry/<session id>.json`, which labels the tab `error` (`limit` for a
  usage limit) until the session's next clean turn end deletes it
- logs `> retry <session> · <time> · <error> · <details>` in the story file, or in
  `~/.claude/flowkit/retries.log` outside a story

Off until you turn it on (`autoretry=on`, which `/handoff:setup` offers), and only while you are
away or awake (`/handoff:awake`: out but reachable): a `server_error` or `overloaded` stop gets one detached waiter per session, which keeps
the computer awake while it waits 2, 5, 10, 20 and 30 minutes, then every 30, for up to 12 hours
from the first error. Then it types `Your last turn ended on an API error (<error>) at <time>. Carry
on from where you were.` into the session's pane, in one tmux call, but only when the session is
idle, its transcript has not moved, the pane is the one its live process runs in, and the pane is
neither in copy mode nor holding a draft. The continue is its own probe: another error starts the
next wait, the count kept. A background session is resumed with the line (`handoff.py wake --id`).
A stop on the output-token limit gets one continue asking for smaller pieces. A `rate_limit` stop
has a waiter of its own (see "Usage limits"); anything else (an authentication failure, an invalid
request) is only recorded.

## Usage limits

New in 0.10.0. A turn that ends on a usage limit (`You've hit your weekly limit · resets 2am`) fires
`StopFailure` with `rate_limit`, and the session sits at its prompt until someone types. Claude
Code's own wait (`autoContinueAtUsageLimit`) does not start in a session with Remote Control on,
and `flowkit-spawn` starts every session with `--remote-control` unless told `--no-rc`; it also
waits only for a reset less than 24 hours away, stops after three continues and skips background
sessions. So a chain that hits the limit at night would still be sitting there in the morning.

On by default, whether you are away or not: the hook starts one waiter per session, which

- learns when the limit resets from the first source that knows: the limit line in the session's
  transcript (it carries the reset the API named), else a fresh `flowkit-usage --live` reading (the
  latest reset among the capped buckets that bind the session: the week, the 5 hours and its
  model's own; never the 10-minute cache or old figures), else the message's words (`resets 2am
  (Europe/Berlin)`). When none of them names one, the stop is tried like an API error: after 2, 5
  and 10 minutes.
- writes one line where the retry line went: `> limit <session> · <time> · continues at <time>, once
  the limit has reset: the computer must be awake then`. It holds a `caffeinate` only from 30
  minutes before that time, not for the days a weekly limit can take; a reset the computer slept
  through is continued once it wakes.
- three minutes after the reset types `Your last turn stopped on the usage limit at <time>; it has
  reset. Carry on from where you were.` into the session's pane, in one tmux call, when the session
  is idle, the pane is the one its live process runs in and is not in copy mode, its prompt box is
  on screen and empty with no dialog below it (a menu's options, an `Enter to confirm` hint), and
  the session has not gone on since the limit line: a reply or a prompt after it means someone
  already took over, while the harness's own lines and a `/usage` do not count. When one of these
  is in the way it looks again every 30 minutes. A background session is resumed with the line.
- stays until the session's next clean turn end, so a continue that hits the limit again (another
  bucket, a reset that came late) is waited out the same way. After three continues with no clean
  turn end in between it stops and says so in the story file, as it does for a session that runs
  outside tmux, where nothing can be typed: `> limit <session> · <time> · not continued: …`. The
  tab keeps its `limit` word until you prompt the session.

The tick starts a waiter again when its process died, at most once a minute. A session
without Remote Control is left to Claude Code's own wait when your settings turn that on
(`autoContinueAtUsageLimit: true`) and the reset is less than 24 hours away.

Off with `autoContinueAtUsageLimit: false` in your `settings.json`, or with `limitresume=off` in
`handoff.conf` (`handoff.py conf limitresume off`); the stop is still recorded and the tab labelled.
New in 0.17.0: a hook of your own can take a stopped session over, and the waiter then stands
down and types nothing (`flowkit-session claim`, see "Sessions from outside").
The record (`~/.claude/flowkit/retry/<session id>.json`) keeps what the waiter knows: `reset`,
`due`, `continues`, a `state` (`waiting`, `continued`, `moved`, `native`, `unreachable`, or what is
in the way: `not idle`, `copy mode`, `draft`, `dialog`, `no prompt box`), the session's status
and bottom pane rows as it stopped and at rest, the login it stopped on (`config`, `account`;
next section), and while one holds the `claim` of a hook outside flowkit (`by`, `until`).

## Two logins on one config folder

New in 0.10.0. Claude Code keeps who is logged in in one file, `.claude.json`: inside
`CLAUDE_CONFIG_DIR`, or in your home folder (`~/.claude.json`) when the variable is unset. So a
session started with `CLAUDE_CONFIG_DIR=$HOME/.claude` and one started without it share `~/.claude`
whole (settings, hooks, plugins, transcripts, flowkit's own files) and differ only in the account
they run on. flowkit works on the folder, so it sees the sessions of both. Where the account
matters it reads that file as the session's own environment names it and compares the account
and organization ids in it; it has no label or variable of its own for a login.

New in 0.10.1: a session owed its Remote Control stays owed however much it writes before its turn
ends (0.10.0 forgot it past 2 MB of transcript), and what is owed is dropped once you bring the
link back yourself.

- `flowkit-usage` keeps its 10-minute answer per account and names the account in `--json`
  (`account`), so one login never answers for the other, nor for the account before a `/login`.
- A usage-limit record keeps the login its session stopped on: `config` (its `CLAUDE_CONFIG_DIR`,
  `null` when it had none) and `account`. Its waiter asks for usage as that login, also when the
  tick started it from the tmux server's environment or the timer's, and refuses a reading that names another
  account.
- **An account change counts as a reset.** A `/login` moves every session of that login to the
  new account. A waiter notices when the ids in its record's account file change and the session
  shows it too: its Remote Control link is gone, or its transcript says it dropped because the
  account changed. The record then starts over: no continue counted, and what it was left to is
  open again (you, after three continues; Claude Code's own wait, which ends at an account change
  without continuing; the tick starts a waiter for those when none lives). The story file gets
  `> limit <session> · <time> · the account it runs on changed: counted as a reset`. Only a live
  reading of the new account counts then, the limit line being the old one's: with no capped
  bucket for the session's model the waiter types `Your last turn stopped on the usage limit at
  <time>; the account it runs on has changed since. Carry on from where you were.` at once; with
  one capped it waits for that reset; with no reading it tries after 2 minutes. A stop within a
  minute of the change (a request already under way) is not counted and is looked at again once
  the minute is over.
- **Remote Control comes back.** At an account change Claude Code drops the link of every session
  on that login, says so in each transcript and reconnects none. The tick sees the change in the
  account files (`~/.claude/flowkit/logins.json` keeps what it last saw there) and `handoff.py
  reconnect` types `/remote-control` into each session that shows no link and whose transcript
  says it lost one that way: once per notice, and only while the session is idle and its prompt
  box is on screen, empty, with no dialog below it. A session at work or on a question gets it at
  its next clean turn end (the Stop hook) or at the tick's next pass; a limited one gets it just
  before its continue. The link has 20 seconds to show. When it does not, a dialog the command
  left open is closed with Esc and the story file gets one line: `> remote <session> · <time> ·
  Remote Control did not come back after the account changed; type /remote-control in the
  session`. A session whose link stands, one that never had Remote Control and one you
  disconnected yourself get nothing, and no session is restarted for it.

Moving a session from one login to the other is not flowkit's: which account a session starts
on, and whether a stopped one may carry on under the other, are yours to decide and to script (a
wrapper that sets `CLAUDE_CONFIG_DIR` by folder, say). What such a script needs of flowkit is in
the next section: where the session runs, a line typed into its pane, and the waiter out of its
way. `driver=background` is single-account:
`claude --bg` has one daemon per config folder, which keeps the environment of whoever started
it first, so every background session runs on that caller's login until the daemon idles out.

## Sessions from outside: `flowkit-session`

New in 0.17.0, for a hook or a script of your own that has to reach a running session: the
commands flowkit's own lines go through, whichever driver the session runs under.

```text
flowkit-session locate <session> [--json]       where it runs, which story it was started for
flowkit-session read <session> [--rows <n>]     the rows its place shows now, as plain text
flowkit-session read <session> --box            one word: free, or what is in a typed line's way
flowkit-session type <session> <text> [--how free|line|over|escape|command]
                                                one line as its next prompt
flowkit-session claim <session> --by <name> [--for <minutes>] [--wait <seconds>]
flowkit-session release <session> --by <name>
```

`<session>` is a session id, a name as `claude agents` lists it, or a process id. `locate` prints
`key: value` lines (`session`, `name`, `pid`, `kind`, `status`, `cwd`, `backend`, `place`,
`place_pid`, `copy`, `story`), one JSON object with `--json`. `type` types only into a free prompt
box unless `--how` says otherwise, and prints the place it typed into or why it did not (`copy
mode`, `no prompt box`, `dialog`, `draft`, `waiting`, `busy`); `read` and `type` take `--place
<id>` in place of a session, for a pane with none in it. Exit codes, the same for all: 0 done, 1
the driver's program did not answer, 2 called wrongly, 3 no such session (or more than one of the
name), 4 it runs in no place flowkit reaches, 5 not done, the reason printed. `flowkit-session
<command> --help` has each in full. On the `PATH` of a session's Bash tool; for a hook outside a
session `/handoff:setup-tmux` and the timer's setup copy it to `~/.local/bin`.

`claim` is the hand-off at a usage limit. A `StopFailure` hook of yours that knows something
better to do with a stopped session (start it again under another login, say) claims it first:
the waiter stands down and types nothing, and the story file gets `> limit <session> · <time> ·
<name> took it over until <time>: flowkit does not continue it meanwhile`. The claim holds for
`--for` minutes (15 without it), while the session runs in the same process, and until `release`
or the session's next clean turn end; given back or run out, the waiter plans the stop again and
continues the session as it would have, with a line saying so. Of a claim and the waiter's
continue that come at the same moment exactly one wins: `claim` exits 0 only when nothing was
typed, and 5 with `flowkit continued it at <HH:MM>` when it came too late. Unclaimed, a stop is
continued as before. A session that started before 0.17.0 was installed runs the older waiter,
which knows no claim, until it is restarted. `handoff.conf` has no key that runs a command at a
stop: what yours does there is your hook's.

[`docs/extending.md`](../../docs/extending.md) lists every place where something of yours meets
flowkit (the flag files, the launch hold, the story file's lines, the sessions file, these
commands, the board's JSON, a launch driver) and says which of it is promised to stay.

## Start-up prompts

New in 0.10.0. Claude Code can ask a question before a session exists: whether you trust the
folder, whether to use Chrome by default. A spawned pane
that gets one sits there unseen: `claude agents` lists no session for it, so no tab says `needs
you` and nothing pushes to your phone. flowkit shows such a pane and never answers it: what a
start-up prompt asks is yours to decide.

**What is detected.** A dialog is a screen whose bottom 8 rows carry its key hints (`Enter to
confirm`, `Enter to select`, `Enter to continue`, `Esc to cancel` or any other `Esc to …` but
`esc to interrupt`). Its first line, the first row with a letter below the last rule row above
the hints (`Accessing workspace:`, `Claude will use your Chrome browser by default …`), is what
every report carries. A prompt that shows no such hints is not recognised.

- `flowkit-spawn`: a pane where no session came up in the 15 seconds and whose screen ends on a
  dialog gets `warning: session '<name>' is parked at a start-up prompt in <pane> (<first
  line>); answer it in the pane` instead of the plain warning, `parked=<first line>` at the end of
  the `SPAWNED` line, and `> parked <name> · <time> · <first line>` in the story file. The close
  that launched it puts the prompt, the tab and the slot under FOR ME.
- The tick, in tmux: a pane that runs claude with no session listed for it, its screen ending
  on a dialog for a minute (since `flowkit-spawn` typed the launch, or since the tick first saw
  the dialog), is marked like a session that waits on you: `NEEDS YOU` on the pane's border, the
  tab's `needs you`, a count in **⚑ n need you**, and a row in `prefix a` with the dialog's first
  line as its name. A claude pane with no dialog on it (`claude agents`, a picker, a `-p` job) is
  left alone, and the mark goes when the dialog does.
- `~/.claude/flowkit/parked.json` lists those panes while they wait: `at` (when the tick last
  wrote it) and `panes`, each with `pane`, `tab`, `index`, `story` (empty outside a story tab),
  `line` and `since`. The tick writes it again at every run and removes it with the last pane; a
  file older than 5 minutes is a tick that stopped (no timer, no tmux client attached) and is read as
  empty. The [`board`](../board/README.md) turns the story's card to NEEDS YOU from it.

**What is prevented.** One prompt can be skipped without deciding anything for you. Until you
have answered Claude Code's Chrome question once (`claudeInChromeDefaultEnabled` in
`~/.claude.json`), every new session asks it; `flowkit-spawn` then starts its sessions with
`--no-chrome`, so they come up without the question and without Chrome, and the question waits
for a session you start yourself. The flag is passed only when `claude --help` lists it (asked
once for each Claude Code version, kept in `~/.claude/flowkit/no-chrome`) and never once you
have answered, whatever the answer. `chrome=on` in `handoff.conf` (`handoff.py conf chrome on`)
leaves it out always, for chains that use Chrome on a machine where the question is still open:
answer it once in a session of your own first.

## Reminders

New in 0.10.0, off until you turn it on (`handoff.py conf remind on`, or `/handoff:setup`). New in
0.10.1: a reminder that reached nobody is tried again after 10 minutes, not at every pass. New in
0.10.2: an idle reporter whose prompt box shows Claude Code's prompt suggestion is rung (0.10.1
read that dim text as a draft of yours, rang nobody and fell through to the next carrier). New in
0.10.3: a rung reporter waits 70 seconds before it pushes. Claude Code takes the typed ring line for
you at that terminal and skipped the push the line asked for (`Not sent`), so no reminder reached
the phone through a reporter; and an answer that says the phone got nothing (Remote Control off)
no longer counts as sent.
Claude Code pushes to your phone when a session waits on you, but some things that wait on you
it cannot see: a pane parked at a start-up prompt has no session yet, a session a usage limit
stopped that flowkit cannot continue (outside tmux, or three continues that ended on the limit
again) looks idle, and a test-queue item turning ready is a line in a file. With `remind=on` the
tick runs `handoff.py remind` at most once a minute, which

- stays silent while you are away (the flag in effect, "Being away": you asked for silence) and
  while you are at the computer: keyboard or mouse input in the last 10 minutes (macOS's HID idle
  time; elsewhere the newest activity of a tmux client).
- says at once, and once, what Claude Code's own pushes cannot see: a pane parked at a start-up
  prompt, a usage-limit stop flowkit cannot continue, a test-queue item ready to test whose gate
  names a device test. Every 2 hours, and only when the set changed since the last time, a
  digest: those plus the sessions waiting on you and every item ready to test, with its `Gate:`.
  An item whose gate starts `merges on green` waits on nobody and is left out; FOR ME steps are
  not reminded (a reporter's final report pushes `<n> for you`).
- writes the text to `~/.claude/flowkit/remind/<stamp>.md` and `~/.claude/flowkit/remind-latest.md`,
  then sends one line under 200 characters (the first thing and `+<n> more in remind-latest.md`)
  through the first of these that carries it:
  1. a story's reporter (`reporter=on`): the oldest idle `<story>-report` in a tmux pane whose
     story has no final report after its last close; never a coordinator. One line is typed into
     its pane as the doorbells type it, only into a free prompt box: `reminder: from flowkit's
     tick, not the user. … first run sleep 70 … run_in_background … when its completion notice
     arrives, send the user one push notification … Message: <the line>`. The wait is there because
     Claude Code takes a line typed into a session for you at that terminal and skips a push for a
     minute after it (`Not sent`): the reporter starts a 70-second wait in the background, ends its
     turn and pushes from the turn the end of the wait opens. It counts as sent once the reporter's
     transcript shows its PushNotification call and the tool's answer within 150 seconds, and not
     when the answer says `not sent` (the terminal active again, Remote Control off, pushes turned
     off in `/config`); then the next reporter is rung. The line says what to do itself, so a
     reporter started before this version does it too.
  2. `notify=<command>` in `handoff.conf`: run through the shell with the line on its stdin, sent
     when it exits 0 (`notify=curl -s -d @- ntfy.sh/<your topic>`).
  3. a macOS notification (`osascript`).
- keeps what it said in `~/.claude/flowkit/remind.json` only once something carried it. A
  reminder that reached nobody is tried again 10 minutes later (the same text, no second file),
  not at every pass, since each try rings every idle reporter; when what waits changes in
  between, the new one is tried at once. One pass runs at a time; one prints a line per reporter
  it rang and what carried the push, or `remind: not delivered at <time>, next try <time>`.

`handoff.py remind --dry-run` prints what a pass would say now and why it would stay silent; it
sends nothing and keeps nothing. The ring line is a line flowkit types: it never counts as you
typing to the reporter ("Being away").

A coordinator of yours that keeps its own reminder timer should drop it when you turn this on, or
both push.

## Keeping the computer awake

The plugin's hooks hold a `caffeinate -i` while a turn runs, and between turns while Remote Control
is attached or you are away or awake (`/handoff:awake`, new in 0.12.0), so a chain of sessions
keeps going overnight. A flag counts from a session's next turn end: one that was already idle
when you set it holds nothing. Each hold is bound to its
Claude process and ends with it. The display still sleeps, and **a closed laptop lid still sleeps
the computer and pauses the chain**: leave the lid open and the power connected. Where `caffeinate`
does not exist (anything but macOS) the hooks do nothing.

## The status line

Claude Code hands a session's context use and cost only to the status line, so a close cannot see
them unless one records them. `/handoff:setup` offers one that does: on every refresh it records
the numbers (the files below) and shows `Opus high · ctx 34% · $1.23`. If you already have a
status line, yours keeps showing: the new command records the numbers, then runs yours. The script
is copied to `~/.config/flowkit/statusline.py` and kept at the plugin's version by a session-start
hook, which does nothing when you have not installed it. Your previous settings are kept as
`settings.json.flowkit-bak`.

What it writes, all under `~/.claude` (your `CLAUDE_CONFIG_DIR`), as one line of JSON each; a
field Claude Code did not send is `null`:

- `session-stats/<session id>.json`, and `session-stats/by-name/<name>.json` for a named session
  (a `/` in the name becomes `-`), rewritten at every refresh: `session_id`, `session_name`,
  `model_id`, `effort`, `cost_usd`, `api_ms` and `wall_ms` (time in API calls, and since the
  session started), `ctx_pct` (context used, percent), `ctx_size` (the context window in tokens,
  new in 0.13.0), `lines_added`, `lines_removed`, `cwd`, `updated` (UTC). The context line below,
  the `board` plugin and a ladder's log read it.
- `usage-stats/week-<reset>-<login>.json`, new in 0.13.0: one file per weekly window of your
  plan, named by the window's reset (epoch seconds) and a key of the login (left out when no
  login can be read). `resets_at`, `account` (the login's account and organization ids, as
  `flowkit-usage --json` names them), `seven_day_pct` (the week used, the highest reading),
  `five_hour_pct` and `five_hour_resets_at` (the 5-hour window that ends then, its highest
  reading), `five_hour_peak_pct` (the highest 5-hour reading of the week), `full_at` (when the
  week first read 100), `reset_at` and `pre_reset_pct` (when the limit was reset before its time,
  and what the week read then), `updated`. `flowkit-usage` falls back to it when Claude Code does
  not answer.
- `usage-stats/series.jsonl`, new in 0.13.0: one line each time a week file changed, with `t`,
  that file's `resets_at`, `account`, `seven_day_pct`, `five_hour_pct` and `five_hour_resets_at`
  as they then were, and the `session_id`, `model_id` and `cost_usd` of the session that moved
  them. It only grows; delete it when you like.

Every session of a login reports the same two windows, and an idle one keeps showing the figures
of its last API call. So a week file keeps the highest reading and is rewritten only when a figure
changes; only a session that made an API call since its previous refresh can report a reset (a
reading 5 points or more under the stored one), and after a reset only such a session raises the
week. Two logins on one config folder get a file each.

New in 0.6.0, a session hears its own context use from that file: a hook on every prompt and
after every batch of tool calls adds one line, `Context ~45%. Repo work: /handoff:close at a
stopping point. A hub or coordinator: a paste-ready /compact Keep: … line under FOR ME.` It comes
on every prompt at 40% or more, and mid-run once at each of 40, 50 and 60%; from a
`/handoff:close` it stays quiet until your next prompt. A reading of 35% or less (after a
`/compact`) lets the steps fire again. Subagents, reporters (`*-report`) and a session's first 30
requests hear nothing, and without the status line the hook stays silent.

## What a close writes

- **The pick-up block**, in the repo's own plan doc under `### WHERE TO PICK UP`, replacing the
  previous one. Which doc is found once per repo (an existing pick-up block, or a `PLAN.md`-like
  file) or asked once, and remembered in the plugin's data directory. The block's shape is
  `skills/close/pickup-template.md`.
- **The next session's prompt**, only when repo work is left: repo, session name, story, the
  sections to read, verified facts, today's items with their checks, the fences, the story's
  Chain end line and the stop condition. Its shape is `skills/close/prompt-template.md`. The
  prompt is saved under `~/.claude/spawned/<name>.md`, launched or not, and that file is the
  record of what the session started with: `flowkit-spawn` launches it as it is, and copies
  into the store only a prompt from elsewhere (a coordinator's brief, a file of yours) and each
  reporter's prompt, stamped; `handoff.py refresh` (every session start) removes those copies
  after 30 days and says how many went.
- **The story file**, `~/.claude/stories/<slug>.md`, when the session's prompt names a `Story:`:
  one section per close with the session id, how to resume it, the four close blocks and a
  `Next:` line: `started <name>`, `queued <name>` (the capacity gate holds the launch),
  `pending <name>` (a saved prompt nothing started), `watching <name>` (the last slice pushed and
  a watch has its pipeline, see "Watches") or `none`. `Next: none` is written only when no repo
  work is left; on the last close (a watch's own section is one), with no session of the story
  still live, it is what marks the story done. Its header carries the story's
  `Chain end:` line (below).

Unattended (`overnight`, the away flag set, or a headless run without the question tool) the close
never asks: it takes the recommended option at every fork and marks it "(auto)".

## Where a chain ends

New in 0.7.3. A chain of spawned sessions used to stop short of what you meant by done: every brief
said "never push", so the last slice landed its commits and waited for your word on the push, the
MR and the merge, which no one was there to give. Now the story carries one line, `Chain end:`,
saying what the chain's last slice leaves behind, and every brief repeats it next to `Stop when:`,
the last slice told that it is the last. The close that drafts a story's first prompt asks once,
proposing `chain_end=` from `handoff.conf` (`/handoff:setup` offers it; `mr-ready` until it is set):
`branch` (no slice pushes, no watch), `draft-mr` (each slice pushes its green commits, the first
push opens a Draft MR; the last slice pushes, registers the watch and closes, and the MR stays in
Draft), `mr-ready` (as `draft-mr`, and the watch takes the MR out of Draft on green; no merge) or
`merge` (the last slice takes the MR out of Draft, sets auto-merge, registers the watch until it is
merged and closes; the watch reports the merge and the target branch's pipeline); your own words
are taken as they are. Since 0.9.0 the last slice does not sit through its pipeline: its close
registers a watch (step 9a, with or without `spawn`) and a red pipeline gets one fix slice, see
"Watches". A slice in the middle of a chain still reads the pipeline of the one before it and fixes
a red it caused. Unattended, the close takes the conf value "(auto)".
The line lives in the story file's header (`handoff.py chain-end <story>` prints it, with a value
or words sets it) and in the pick-up block. The close itself still never pushes: the slices do, as
the line says.

## A slice that starts with a command

New in 0.7.7. Some skills only you can start: one with `disable-model-invocation: true` answers
"type /x" instead of running, so a spawned slice whose work begins with it stopped until you typed
the command yourself. Claude Code runs a first prompt that starts with `/` as your own command, so
when the next slice is driven by such a command the close (or `/handoff:spawn`) saves the command
line alone as the prompt, for example `/implement-story 1526354`, and `flowkit-spawn` launches it
as `claude … "/implement-story 1526354"`. Every word after the command's name reaches the skill as
its arguments, which a skill may read as answers to its own questions, so the brief never rides
along: it goes, in full, into the `Brief:` line of the repo's pick-up block, and `flowkit-spawn`
appends one system-prompt note telling the new session to read it there and follow its Chain end,
Report-to and Stop when. `handoff.py save-prompt` refuses a command prompt with more lines and
puts no `Report-to:` line into one. Every other prompt is launched as before.

## A helper reports back

New in 0.7.6. A review or investigation launched beside a story's chain used to end in its own
tab, and the session that launched it learned the verdict only when you pasted it over. With
`report_to=on` in `handoff.conf`, the brief of such a helper carries `Report-to: <session>` in its
header, naming the session that launched it (`handoff.py self` prints a session's own name, from
`~/.claude/sessions/$CLAUDE_PID.json` or the `claude agents` row of its parent process; `handoff.py
state` prints the key as `report-to:`), and its `Stop when:` ends with the send: at its close the
helper sends that session one message, the verdict in one line, the findings by number with a
worth-fixing word, the links, at most ten lines (the close skill's step 8, the one exception to
"do not send unless asked"); the launching session relays it in plain words and acts on it as
your rules say. Which launches carry it: `/handoff:spawn` with a prompt (`report_to=<name>` or
`report_to=none` on the command override the conf; `save-prompt <name> --report-to <session>`
writes the line, on the `Repo:` line of a brief or as a first line of its own), and a close's
helper beside the chain; never the chain's next slice, never a `single` without a prompt, and a
prompt whose header already names a `Report-to:`, or that carries a coordinator's `REPORTING`
line, is left as it is. A name instead of `on` sends every helper's verdict to that session; off
by default, since a colleague's spawns have nowhere to report. The caveat: a message between
sessions of different permission modes waits for the receiver's approval, one to a session parked
on a question waits with it, and a stopped background session gets none; so the helper's close
and DONE carry the verdict too, and a held or failed send loses nothing but the ring.

## Capacity

A machine runs a few chains at once, and some things a repo has only one of: an iOS simulator, an
Android emulator, a local backend, a dev-server port. `flowkit-capacity` (`bin/`, over
`scripts/capacity.py`) counts the first and leases the second, and `flowkit-spawn` is the gate that
holds every story launch to both.

New in 0.11.0: slots counted by weight, a prompt's `Kind:` saying what a launch counts as ("Slots
by weight"); a free resource with a `stop` line stopped once it sat free ("A free resource is
stopped"); a chain is a story in a working tree, so other stories in the same checkout count as
chains of their own; a queued launch that ends with no session is queued again instead of lost.

The numbers live in `${XDG_CONFIG_HOME:-~/.config}/flowkit/capacity.conf`; with no file a machine
runs 3 chains and declares no resources:

```
slots = 3                   # chains at once on this machine
slice_minutes = 50          # ETA fallback until the log has history
coordinator_growth = 300k   # context tokens a coordinator may add per day
stop_minutes = 20           # how long a resource with a `stop` line sits free before it is stopped

[/Users/me/app]             # a repo's main checkout; its worktrees inherit the section
slots = 2                   # optional: at most 2 of the machine's slots for this repo
checkouts = 1               # optional: its sessions switch branches in this one checkout
check = ./ci-status         # optional: what a watch asks for this repo's merge requests
resource simulator = install a build, run a device check, hand over a test
resource backend   = start the local stack; release once it is stopped
audit simulator    = xcrun simctl (install|launch)|maestro test   # optional: the audit hook's pattern
stop simulator     = /Users/me/bin/stop-simulators                # optional: what stops it once it sat free
```

New in 0.13.0, `/handoff:setup` writes a starter file by asking two things: how many chains at
once, and what the repository you are in has only one of. It runs `flowkit-capacity init --slots
<n> [--repo <path> --resource '<name>=<what a session does with it>' ...]`, which prints the file
it wrote and leaves a file that exists exactly as it is; everything past `slots` and the
`resource` lines is yours to add by hand.

Resource names are machine-wide: two repos that both declare `simulator` share one lease. So are
`audit` patterns (Python regular expressions, searched in a Bash command) and `stop` commands (a
second, different one for the same name is refused with one stderr line).

`checkouts = 1` is for a repository whose sessions all work in its main checkout and switch
branches there, so one works in it at a time: the checkout becomes a resource of its own,
`<repo dir>-checkout` (`app-checkout`), which every launch into the main checkout takes without
naming it in `Needs:`. A slice there may still take a device. `check = <command>` is the command a
watch runs for a merge request of this repository in place of asking GitLab (exit 0 green, 1 red,
2 still running; see "Watches").

**Slots are counted, never leased.** A slot is a story with a live session in a working tree, read
from `claude agents --json --all` and the story files each time it is asked, so a crashed or resumed
session cannot leak one. A session belongs to a story when the story file names it (a close
heading, a `Next:` line, a `> launched <name>` line). Reporters (`<story>-report`) and coordinators
do not count, nor a session whose close is recorded (by its `Session:` id) unless it is busy again;
a review in the same tree as the next slice shares its slot, while sessions of other stories in that
tree (a main checkout several stories read in) count as chains of their own; a chain whose sessions
sat 2 hours with no close shows as `stalled` and stops counting. When `claude agents` fails, hangs or answers in a
shape it does not know, the count is unknown, never zero: nothing is reclaimed.

**Slots by weight.** A chain counts one slot unless the file gives kinds of work a weight:

```
slots = 8
weight review = 1           # what a chain of this kind counts toward the slots
weight implementation = 2
weight test = 4
unmarked = implementation   # the kind a session with no Kind: line counts as

[/Users/me/app]
resource simulator = install a build, run a device check, hand over a test
kind simulator = test       # a chain holding the simulator counts as a test
```

A kind is a word of your own (letters, digits, `.`, `_`, `-`; case does not matter). `weight` and
`unmarked` are machine lines, above the first section; `kind <resource> = <kind>` belongs to a
section, holds machine-wide like the resource's name, and names a kind that has a `weight` line.
A launch says its kind in its prompt's header, `Kind: implementation`, and the gate keeps it as
`work` on the log line, the `launching/` marker and the queue entry.

- A chain counts the largest of: its sessions' kinds (each session's latest start in the log, a
  marker's own) and the kinds that the resources it holds imply. Sessions of one story sharing
  a tree count once, at the largest.
- A session in neither the log nor a marker is unmarked, and so is a kind with no `weight` line
  (one stderr line at the gate). Unmarked counts as the `unmarked` line's kind, or 1 without that
  line.
- Your own test hold on a resource that implies a kind counts that kind's weight once, however
  many such resources it holds, until it runs out.
- `used` is the sum, for the machine and for a repo with its own `slots` (a `limit` moves that
  number as before). A new chain counts as its `Kind:` or as what its `Needs:` imply, whichever
  is more, and starts when the sum plus that is within `slots`, or when nothing else is counted:
  a launch heavier than the whole budget still starts on an empty machine. Otherwise it is queued
  with both numbers, `slots 7/8, implementation needs 2`.
- A chain's own next slice and a watch's launch never wait, as before, so the sum may run over
  `slots`; new chains then wait until it is back under.
- `status` ends each chain's line with its kind and weight (`· implementation 2`) and lists a
  counted test hold as `chain yours · test hold until 12:30 · test 4`.

With no `weight` line every chain counts 1, whatever its `Kind:` says and whatever it holds.

Choosing the numbers: `slots` is the budget in units of the lightest kind, and a weight says how
many of those one chain of a kind stands for, by what it takes of the machine next to the others
and how fast it spends usage. Start from the most the machine has carried without trouble (the
report's peak of sessions at once and its minutes over the slots), then read the numbers back as
mixes: `slots` divided by a weight is how many chains of that kind run at once on their own (8
with implementation 2: four), and what is left beside one of the heaviest is what runs during it
(8 with a test at 4: two implementations, or four reviews). The file is read at every gate call,
so a number can move at any time.

The weights count only what the prompts say. Every word a `Kind:` line may carry needs a `weight`
line spelled the same, since a word with none counts as unmarked. The close writes the line from
its prompt template (with a ladder, the ladder's kind for the slice: planning, investigation,
implementation, review, docs or config), the `coordinate` plugin's briefs carry it, and a `waves`
item names it as its fourth field. A prompt typed by hand, or drafted before the prompts carried
the line, has no kind: set `unmarked` to what such a session most likely is, and expect the sum to
read high until the chains started that way reach their next slice.

**Resources are leased to a session id**, all of a set or none:

| Command | What it does |
|---|---|
| `flowkit-capacity status [--json]` | One line for the slots, then one per repo limit, chain, resource, queued launch and open watch. |
| `flowkit-capacity take simulator,backend` | Leases the set to this session, or nothing if any of it is held; refused while the session holds something outside the set (release first: nobody waits for more while holding some). |
| `flowkit-capacity release [names]` | Gives back what this session holds, all of it without names. |
| `flowkit-capacity hold simulator --for me --minutes 60` | Your test hold: no slice takes it until the minutes run out or `release --for me`; run it again to renew. |
| `flowkit-capacity drain` | Launches what the queue holds that can start now (it also runs on its own, see below). |
| `flowkit-capacity idle` | The minute pass: stops a resource with a `stop` line that sat free for its minutes (the tick runs it, see "A free resource is stopped"). |
| `flowkit-capacity waves a=/Users/me/app:2:simulator b=/Users/me/lib [--story s --write]` | Plans a batch (`NAME=TREE[:SLICES[:NEEDS[:KIND]]]`, needs joined by `+`, in launch order; `b=/Users/me/lib:1::review` names a kind and no need): each item's wave, start and ready times, and one plan line. See "Waves" below. |
| `flowkit-capacity testq <story> [set\|drop <item>] [--state …] [--eta HH:MM] [--mr …] [--device …] [--gate …] [--launch name]` | The story's test queue, `<story>.tests.md`, one line at a time under the lock, each change logged as `test`. The file goes once its last line does. `--gate` sets what the item waits for before it merges ("The test queue's fields" below). `--launch` ties a line to a queued launch: its drain sets it `running` with its ready time. |
| `flowkit-capacity limit /Users/me/app -1 [--hours 12]` | The repo runs one slot fewer (never under one) for the hours given; `0` ends it. Logged as `limit`. |
| `flowkit-capacity report [--since 7d] [--until …] [--baseline] [--against base.json] [--json]` | The numbers of a week under the gate: see "Report" below. |
| `flowkit-capacity note collision "two installs on the simulator"` | A line for the report, from any session or by hand, when two things met on one resource. |

A lease goes free on its own when the holder's close is recorded, when the holder has no pid, or
when it has been not busy with its transcript untouched for 30 minutes; never while `claude agents`
is unknown (a test hold still runs out on the clock).

**A free resource is stopped.** A simulator left booted after its last user holds memory for
nothing. Give the resource a `stop <resource> = <command>` line and it is stopped once it sat free
for `stop_minutes` (20):

- The clock starts when a lease on the resource ends, and a pass of its own keeps it:
  `flowkit-capacity idle`, which the tick starts detached at most once a minute while
  the file has a `stop` line (with neither the timer nor tmux, run it from cron). It costs one file read with no such
  line, and no `claude agents` call while no such resource is leased or counting. A lease shorter
  than a minute between two passes is not seen. A resource nobody leased has no clock: one you
  started by hand, and one that was stopped and that you start again, is left alone until a
  session next takes it and gives it back.
- A lease ends the spell: a session's, a launch's, your test hold. A command a session runs on the
  resource without the lease (an `audit` match) counts the minutes from that command again; it
  never starts a clock. A resource a queued launch waits for is left up. Nothing watches your own
  hands: say that you are testing, and the session you tell runs
  `flowkit-capacity hold <resource> --for me`, after starting the resource when `take` or `status`
  says it was stopped.
- When the minutes are over the pass takes the resource itself for as long as the command runs
  (`held by idle stop`), so nobody takes it mid-stop, and runs the command outside the lock:
  through `sh -c`, from your home directory, with the tick's environment (give absolute paths),
  killed with everything it started after 60 seconds. A pass that does not get the lock within a
  second leaves that minute out.
- Exit 0 ends the spell: the command runs once per free spell. Any other exit, or the timeout, is
  tried again by the next pass, five times in all; then the spell ends as failed, with one macOS
  notification. So let the command prove its own result: stop, then look, and exit 0 only when
  nothing is left running. Every try is a `stop` line in the log. `flowkit-capacity status` shows
  `free · stopped 14:20` or `free · stop failed (exit 1) 14:25` until the resource's next lease,
  and the `take` that follows a stop says `simulator was stopped at 14:20, left free: start it
  first`.

**The gate.** Every story launch through `flowkit-spawn` (`--single` excepted) asks it first,
after the refusals and before anything is typed:

- A launch that opens a **new chain** while the machine's slots, or its repo's, are all used, or
  while `claude agents` does not answer, is not launched but queued: `flowkit-spawn` prints
  `QUEUED #<position>, ~<HH:MM> · id=<id> · <why>`, writes `> queued <name> · …` into the story
  file and exits 11. The time is the waves forecast's (below). `--over-cap` launches it anyway,
  logged as `over-cap`.
- A chain's **own next slice** never waits for a slot: the launching session belongs to the same
  story (or, named in no story file, runs in the tree the launch goes to: a first slice started by
  hand). A coordinator's or a reporter's launch is always a new chain, as is a launch of another
  story. A launch into a tree that already holds a counted chain of its story (a review beside the
  next slice) needs no slot either.
- The prompt's header (the lines before its first blank one) may carry `Needs: simulator, backend`,
  `Left: 2` (slices left after this one, for ETAs) and `Kind: implementation` (what the chain
  counts, "Slots by weight" above). What `Needs:` names is taken all or none
  for the launch, before anything waits; a name no section declares is ignored with one stderr
  line. A continuation's closer hands over what it holds only when every need is free at once;
  otherwise it gives back everything it holds and the launch is queued holding nothing, so two
  closers crossing over a simulator and a backend never wait on each other.
- The queue is first in, first out: a launch never passes an earlier one waiting for a slot, or
  for a resource it needs too.
- A launch a watch makes (`flowkit-spawn --from-watch <watch>`: a red pipeline's fix slice, an
  on-green prompt) is never the calling session's own next slice. It never waits for a slot, since
  its story's slot went back when the last slice closed (logged `over-cap`); it waits for its
  needs like any other, goes first when the queue drains, and a chain's next slice whose closer
  holds a resource the fix waits for (the one checkout) is queued behind it.
- The launch's holds go to the new session once `flowkit-spawn` prints `SPAWNED`, and back on any
  other exit (a refusal, a failed start, a kill: an EXIT trap, and the `launching/` marker goes
  with the pid). Every story launch writes `> launched <name> · <time>` into the story file,
  which is what makes the new session count before its first close.

**The queue drains itself.** `capacity.py drain` launches, under the lock and oldest first,
every queued launch whose slot and needs are free, unless a load hold is set, as a detached
`flowkit-spawn --from-queue <id>` with the arguments it was queued with (where it was to launch
included, as `--target`; output in `capacity/launched/<id>.log`). It runs on every recorded close
(`handoff.py record`, which counts the closing session as gone though it is still busy), every
release, every launch that ends, and the tick (`flowkit-tick`, detached, at
most every `FLOWKIT_CAPACITY_POLL` seconds, 15 by default). An empty queue costs one directory
read. With the background driver, no timer and no tmux there is no tick: the queue drains on those
events only.

**A queued launch that fails is queued again.** A drained `flowkit-spawn` that ends with no session
(a refusal such as uncommitted changes in the tree, a tmux window it could not open, a process
that died) puts its entry back under its id, so it keeps its place: tried again 5 minutes on,
the first line that try printed as its `why` (`launch failed: …`), logged as `requeue`, and at the
first failure a `> launch failed <name> · … · queued again` line in the story file. After 6 failed
tries it leaves the queue (`give-up`), the story file says to launch it by hand and a notification
tells you.

**Waves.** `waves` and every queued ETA replay the drain's rules forward in time: the running
chains end after their current slice and the `Left:` count of their latest launch, the queue goes
first, then the batch in order; a new chain waits for a slot (for room for its weight, each running
chain at the weight it counts now), every item for all its needs at once,
and nobody passes an earlier one waiting for a slot or the same resource. A slice lasts its repo's
median from the log (from a slice's launch to its own gate call for the next one, the minutes that
call sat in a load hold taken off, spans over 12 hours left out), or `slice_minutes` until a repo
has three spans. A running slice already past its median is given ten minutes yet. Every time is
printed with `~`: it is a forecast. An item that needs no resource is `shed`: a coordinator gives
it a chain of its own. The plan line reads

```
Plan: 1 of 3 now (a) · 1 queued (c ~16:40) · 1 shed (b) · slots 3/3 · 1 wait on your testing
```

the last part counting the story's test-queue lines whose state says `to test`, less those whose
gate starts `merges on green`. With `--write`, every item that does not start now gets a
test-queue line (`queued, wave 2, needs simulator`, its ready time) tied to its launch name.

**The test queue's fields.** A line is `item · MR · device · state · ETA`; a sixth field and on
are notes, kept as written whatever `testq set` changes. The **gate** is what the item waits for
between a green pipeline and its merge: a whole field that starts `Gate:`, set with `testq set
<item> --gate "<text>"` (an empty text removes it). The first such field after the item counts; a
second one stays a note, and "held at the gate" inside a sentence is no gate. The text is free and
flowkit knows one rule about it: a gate that starts `merges on green` waits on nobody, any other
(and a line without one) waits on you once its state says `to test`, and `device` anywhere in it
makes it a device test. Which gates a repository has is the repository's to say, in its
`CLAUDE.local.md` or `CLAUDE.md`; a coordinator reads them there at planning (the `coordinate`
plugin, `/coordinate:start`).

**The audit hook.** A `PreToolUse` hook on Bash (`capacity.py audit`) logs `audit` when a command
matches a resource's `audit` pattern and the session running it does not hold that resource: the
resource, the session, who holds it (or nobody) and the command's first 200 characters. When
nobody holds a resource that has a `stop` line, the match also counts its free minutes from that
command again. It never
denies and prints nothing; with no `capacity.conf`, or no `audit` line, it costs one file check.

**Report.** `flowkit-capacity report --since 7d` reads the log and the transcripts for a window:

- launch holds (launches that sat in a load hold first) and their minutes;
- queued launches, the minutes they waited, the median launch ETA error (the queued line's time
  against the drain) and the median ready ETA error (a test-queue line's first ETA against the
  moment its state first says `to test`). A wait counts when it ended (its `dequeue`) or while
  its entry is still in the queue, to the window's end; a launch that left the queue by hand
  (its `queue/<id>.json` removed) counts nothing and is told beside them, `1 more left the queue
  by hand`;
- resource waits per resource (queued launches whose reason names it) and their minutes;
- reclaims and over-cap launches;
- idle with a queue: a drain that launched an entry logs `gap`, the time since the drain before it
  (which could not start it), so the gap bounds how long the entry sat startable. Gaps over 10
  minutes count, split by day and by night (22:00 to 08:00, keyed by the evening's date);
- collisions: `audit` lines and `note collision` lines;
- batches: `waves --write` runs, those of 4 items or more;
- watched: the minutes the watches that ended in the window were open, how many they were, and
  the fix slices they started. A watch's own log lines, and the gate's for a launch a watch made,
  are in no other count;
- from the transcripts (`projects/*/*.jsonl` touched in the window): the peak of sessions at once
  and the minutes over the machine's slots (a session counts while it wrote within 30 minutes),
  and per coordinator (a name like `*-coord`, or on a coordination board) per day: its context
  growth (the coordinate plugin's `growth()`, the 24 hours up to that midnight, or to now on the
  last day; it needs that plugin installed beside this one), its compactions, the prompts typed to
  it (Remote Control included; a peer session's message, a hook's text or a task notification is
  not typed) and those asking for status.

It ends with the battle test's pass line: enough batches (3 of 4+), no night idle with a queue over
an hour, median ready ETA error at most 30 minutes, every coordinator day under
`coordinator_growth`, status asks per day at most half the baseline's, no collision. `--baseline`
reads the transcripts and story files only, for a week before the gate ran; save its `--json` and
pass it as `--against` to the battle test's report.

State lives under `${CLAUDE_CONFIG_DIR:-~/.claude}/capacity/`: `resources/<name>.json` (the
leases), `launching/<pid>.json` (an in-flight launch, counted while its pid lives), `queue/<id>.json`
(a queued launch: its arguments, prompt, story, tree, needs, left, work, by, queued-at), `log.jsonl`
(one line per take, release, free, reclaim, hold, stop, launch, queue, dequeue, requeue, give-up, gone,
repoint, over-cap, test, limit, waves, audit, note and watch; `reclaim` when the holder died; `stop`
is one try of a `stop` command, with the `resource`, `since` (what its free minutes were counted from), `minutes`,
`code` (the exit code, or `timeout`), `seconds`, `tries` and, when it failed, its last line of
`output`; launch, queue and dequeue carry
the repo, `Left:`, `Kind:` as `work` and, for a gate call, the launcher and its load-hold seconds; dequeue its `gap`;
queue and over-cap carry `chains`, the chains counted then: each one's `name` (its sessions; its
story while it launches; `yours` for your test hold), `state` (`busy` when one of its sessions is,
`idle`, `launching` or `hold`), `holds` (its resources), `kind` and `weight`, or `null` when
`claude agents` did not answer; `gone` is a queued launch that left the queue with no drain, its
file removed by hand, logged once by the next gate call, drain, take, release, hold or status;
the gate's lines for a watch's launch carry `watch`),
`drained` (the last drain of a non-empty queue), `limits.json`, `testq.json` (test-queue lines tied
to queued launches), `idle/<name>.json` (the minute pass's stamp for a resource with a `stop` line:
`held`, `free` with `since` and `tries`, `stopped` or `failed` with `at`; beside it `<name>.used`,
the time of the last command a session ran on it without the lease) and
`status.json`, rewritten on every change with a `version` field (3 since it lists the open
watches as `watches`; a chain's `kind` and `weight` came later without a new version;
`../board/tests/fixtures/capacity-status.ts` shows its shape). Every change runs under one lock that is
tried for 10 seconds and then gone on without, with one stderr line, so a stuck holder never
stalls the machine.

## Watches

New in 0.9.0. A chain's last slice used to sit in its session until the pipeline of what it pushed
was green: a slot, a device lease and a working tree held for an hour of waiting. Now nobody waits
for green, and red wakes somebody. The last slice pushes, its close registers a watch (step 9a)
and records `Next: watching <name>`, and the session is over: its slot, its leases and its tree are
free that minute. A script asks for the pipeline once a minute, at no tokens and no slot. Only
starting work is optimistic: a merge you did not ask for, a release and a message to a person
still wait for green and for your word.

```
flowkit-watch add --story <slug> --mr <url> [--ready] [--until merged] [--left <text>]
flowkit-watch add --story <slug> --ref <branch> [--sha <sha>] [--project <path>] [--name <n>]
flowkit-watch add --story <slug> --check '<command>' --name <n>
    any of them: [--on-green-run '<command>'] [--on-green <prompt> [--repo <tree>] [--on-green-name <n>]]
flowkit-watch list [--json]
flowkit-watch drop <name>
```

**Registering.** `add` runs in the tree that was pushed and arms the sha of `@{u}`, not `HEAD`: a
close commits its docs after the push. It asks GitLab nothing, so a close never waits for the
network. It keeps `glab`'s path from the session's PATH, the tree and its branch, the session that
registered it with its close's LEFT, and the model, effort and `Needs:` of that session's prompt.
A watch's name is its own: `<repo dir>!<iid>` for a merge request (`app!12`), `<repo dir>@<branch>`
for a ref, `--name` for a command, and `…#2` for the next watch of the same target, so a section
written for one never stands for the next. There are three kinds: a merge request (its head
pipeline, through `glab api`, counted while the MR's head is the sha that was armed; or the
repository's `check = <command>` of `capacity.conf`), a ref (a branch's pipeline at a sha) and a
command (exit 0 green, 1 red, 2 still running, anything else an error; its last line of output is
the summary).

**The poller.** One per machine (`poller.pid`, whose mtime is its heartbeat), started by `add`
and gone when no watch is open. It asks each watch once a minute, 30 seconds at most per ask,
under no capacity lock, with `CLAUDE_PID`, `TMUX_PANE` and `CLAUDE_CODE_*` taken out of its
environment so that nothing it starts passes for a session, and keeps the computer awake
(`caffeinate`, where there is one) while a watch is pending. The tick, every recorded close and every `flowkit-spawn` that ends start
it again when it is gone or its heartbeat is older than three minutes.

**What an answer does.** Pending does nothing. Everything that ends a watch is a section of the
story file under the watch's name, written through `handoff.py record`, so the reporter is rung
and the capacity queue drains; what does not end it is a `> watch <name> · … · <time>` line there,
which stands for no promise.

- **Green**:
  ```
  ## app!12 · 2026-11-17 16:05
  Watch: pipeline green 16:04 · sha abc1234 · 41 min · https://gitlab.com/g/p/-/merge_requests/12
  Next: none
  DONE - pipeline green
  FOR ME - nothing new
  OPEN - nothing
  LEFT - <the registering close's LEFT>
  ```
  With `--ready` the watch takes the merge request out of Draft first. `--on-green-run` runs a
  command in the tree (five minutes at most; its exit and last line go into DONE), and
  `--on-green` starts a session with that prompt, in `--repo`, named `--on-green-name` (the prompt
  file's name by default), recorded as `Next: started <name>` or `queued <name>`: "when this is
  green, start that".
- **`--until merged`** (the chain end `merge`): after green the watch waits for the merge. A
  missing approval is a line and the wait goes on; a conflict or an auto-merge a push cancelled is
  reported. Once merged it watches the target branch's pipeline at the merge commit and reports the
  result; a red target is reported, never fixed (a fix there is a new merge request: your call).
  Then, when the tree is the main checkout on that branch, clean and nobody works in it, it
  switches to the default branch, pulls and deletes the merged branch.
- **The head moved** (someone pushed, the pipeline was cancelled for a newer one): the watch moves
  to the new head with a `retarget` line, and never starts a fix for a sha that is no longer the
  head.
- **Red**, the first of a merge request: one fix slice, marked "(auto)". The watch fills
  `scripts/fix-brief.md` in as `~/.claude/spawned/<story>-fix-<n>.md` and launches it through
  `flowkit-spawn --from-watch <name>`: in the registered tree when that is still on the branch
  and free (no session, queued launch, test hold or other process in it, nothing uncommitted); in
  a `checkouts = 1` repository, queued for the checkout; else in a detached worktree under
  `.claude/worktrees/`, which goes when the watch ends. An effort of `max` is lowered to `xhigh`,
  since nobody approved it. The red's section says `Next: started <session>` or `queued
  <session>`, the watch stays open as `fixing`, and the fix slice's close registers the next
  watch (`app!12#2`) with a bare `flowkit-watch add --story <slug> --mr <url>`, which keeps the
  flags of the one before. A red after one fix is reported with its failed jobs: the second is
  yours. A red command or ref is reported too.
- **A red base.** A merge request whose target is the branch of another watch that is red waits
  for it (`waiting-base`): the base is fixed in its own story.
- **Reported**, each a section with `Next: none`: no pipeline for the armed head after 15 minutes;
  a pipeline that is `manual`, `canceled` or `skipped`; 15 asks failed in a row (`watch broken:`
  with the error: `glab`'s login, the network, a command that cannot run); six hours since the
  watch was armed, the time it spent `fixing` or `waiting-base` aside.
- `flowkit-watch drop <name>` ends a watch with a section of its own (`Watch: dropped`).

**What reads `Next: watching`.** It is a promise like `started`: the story is not complete until
the name has its own section. The tmux kit shows the story's tab as `watching` while nothing in it
is more urgent, closes the pane of the slice that registered the watch, and does not ring the
reporter for a final report while a watch is open; the reporter writes that report once every
`watching` name has its section. `flowkit-capacity status` prints a `watch` line per open watch
and `status.json` lists them as `watches`, rewritten whenever one opens, changes state or goes;
the [`board`](../board/README.md) plugin reads them there, so a story of two repositories is not
done while its second watch runs. A line of a story's test queue that names the merge request
moves with it: `building` with an ETA (the repository's median from registering to green, once
the log has three), `fixing`, `ready to test`.

State lives under `${CLAUDE_CONFIG_DIR:-~/.claude}/flowkit/watches/`: `<name>.json` (one watch;
the file goes once its last section is recorded), `<name>.lock`, `poller.pid` and
`launched/<session>.log` (the output of a launch the watch made). Events go to capacity's
`log.jsonl` as `watch` with an `op`: add, arm, retarget, green, red, fix, report, merged, error,
deadline, done (how it ended, and its minutes).

## Your own ladder

A close can choose the next session's model and effort, and run a step of yours, from one file:
`~/.config/flowkit/ladder.md` (`$XDG_CONFIG_HOME/flowkit/ladder.md` when that is set). It lives
outside the plugin so an uninstall or an update never touches it. Without the file the close skips
this step and the next prompt carries no model or effort: the next session runs at your defaults.

The file is plain instructions in two sections, which the close reads and follows:

- `## Rung`: how to pick Model, Effort and a one-line Why for the next session from its kind
  (planning, investigation, implementation, review, docs or config).
- `## Close step`: what to do at every close, before the story record. It may run your own
  scripts; give them by absolute path.

A model or effort you name in the session wins over the file. A worked example, a fixed table
plus a log line:

```markdown
## Rung

| Kind | Model | Effort |
|---|---|---|
| planning | opus | high |
| investigation | opus | medium |
| implementation | sonnet | high |
| review | opus | medium |
| docs or config | sonnet | medium |

Why line: "<kind> from the fixed table".

## Close step

Append one line to `~/notes/sessions.log`:
`<date> <this session's name> <kind> <model>/<effort> next=<next session's name or none>`.
```
