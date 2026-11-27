# ladder

A fixed model ladder for [`handoff`](../handoff/README.md) closes: for each kind of session
(planning, investigation, implementation, review, docs, config) a model and an effort, which the
close writes into the next session's prompt. Nothing moves on its own; you change a row when you
want to.

Install with `claude plugin install ladder@flowkit`, then `/reload-plugins` or a new session, then
`/ladder:setup`.

## Setup by plan

`/ladder:setup` reads your plan (`claude auth status`) and proposes its defaults; it shows the
file as a diff and writes it only on your yes.

| Kind | Max, Team, Enterprise | Pro, API key, unknown |
|---|---|---|
| planning | opus / xhigh | opus / high |
| investigation | opus / high | sonnet / xhigh |
| implementation | opus / medium | sonnet / high |
| review | opus / high | sonnet / xhigh |
| docs | sonnet / high | sonnet / high |
| config | sonnet / high | sonnet / high |

No row starts at effort `max`, and `max` is off.

## Where the table lives

In `~/.config/flowkit/ladder.md` (`$XDG_CONFIG_HOME/flowkit/ladder.md` when that is set), the
file `handoff` reads at its ladder step ("Your own ladder" in its README). It is outside the
plugin, so an uninstall or an update never removes it. Its first line is
`<!-- flowkit-ladder -->`: setup and edit change only a file with that line, so a ladder of your
own in that place stays as it is (move it away first to use this one). Its `## Close step` does
nothing.

## Edit

`/ladder:edit` changes a row (`implementation=opus/high`) or turns `max` on or off, as a diff
you approve. A row at `max` needs `max` on. The helper does the same by hand:

```
python3 <plugin dir>/scripts/ladder.py show
python3 <plugin dir>/scripts/ladder.py diff implementation=opus/high
python3 <plugin dir>/scripts/ladder.py write implementation=opus/high
```

## The effort gate

Silent until you turn `max` on. From then, a Bash command that launches a Claude session at
effort max (`claude --effort max`, `flowkit-spawn --effort max`, `CLAUDE_CODE_EFFORT_LEVEL=max`)
asks for your yes first, even in auto mode; while the `/handoff:away` flag is set it runs at
`xhigh` instead. Set means from the moment the flag is written: handoff 0.10.0 stamps it five
minutes ahead for the coordinators' last call, and `/handoff:close overnight spawn` launches
within those minutes, so they count here (new in 0.1.2; 0.1.1 asked until the stamp had passed).

New in 0.2.0: the gate reads a ladder of your own too. One line of the file, alone on its line,
arms or disarms it: `max: on` or `max: off`.

| `ladder.md` | `max: on` | `max: off` | no such line |
|---|---|---|---|
| this plugin's table | asks | silent | silent |
| a ladder of your own | asks | silent | asks |
| no file | | | silent |

A ladder of your own that never heard of the line is armed: a file written before the line existed
must not switch the gate off, and a ladder you wrote yourself is the one that may pick `max`.
Until 0.1.2 the gate never fired for such a file, so if you have one and this plugin installed, a
launch at effort max now asks (and runs at `xhigh` while you are away); `max: off` in the file
brings the old behaviour back. `ladder.py show` prints which it is.

## Tests

`python3 -m unittest discover plugins/ladder/tests` from the repo root.
