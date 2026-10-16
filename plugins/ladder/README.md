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
effort max (`claude --effort xhigh`, `flowkit-spawn --effort xhigh`, `CLAUDE_CODE_EFFORT_LEVEL=xhigh`)
asks for your yes first, even in auto mode; while `/handoff:away` is set it runs at `xhigh`
instead. It reads only a table this plugin wrote, so with a ladder of your own it never fires.

## Tests

`python3 -m unittest discover plugins/ladder/tests` from the repo root.
