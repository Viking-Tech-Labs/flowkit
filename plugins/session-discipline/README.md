# session-discipline

Working-session conventions that you adopt into your own `CLAUDE.md`, and a decision-interview
skill. The conventions arrive as one block between two marker comments. It is your file: you see
every rule, each says what it replaces, and you delete the ones you do not want.

Install, after adding the flowkit marketplace (see the root README): `claude plugin install session-discipline@flowkit`.

| Skill | What it does |
|---|---|
| `/session-discipline:adopt [path]` | Shows what is installed against what is available, then writes the block if the file has none. Run twice, it writes once. |
| `/session-discipline:adopt status [path]` | The state of the block (none, current, differs from the plugin's, edited by hand) and the flowkit plugins installed and available. Writes nothing. |
| `/session-discipline:adopt update [path]` | Shows what would change, then rewrites the block between its markers. Asks first when you edited the block by hand. |
| `decision-interview` | How Claude gets a decision or a missing fact out of you: investigate first, every question with a recommended answer, asked as choices, written down at the end. Claude uses it on its own when a fork is expensive to reverse; you can also call `/session-discipline:decision-interview`. |

`adopt` runs only when you type it. Nothing in this plugin writes to your files otherwise: no
hook, nothing at session start.

## Which file

With no path the block goes into your own `CLAUDE.md` (`~/.claude/CLAUDE.md`, or the one in
`CLAUDE_CONFIG_DIR` when that is set), since these are habits for every repository. Give a path
to a project's `CLAUDE.md`, or to its directory, to adopt them for one project instead. A missing
file is created.

## The block

```
<!-- flowkit:session-discipline begin v0.1.0 -->
## Session conventions
...
<!-- flowkit:session-discipline end -->
```

The text is `block/conventions.md` in this plugin: how a session closes (`DONE`, `FOR ME`, `OPEN`,
`LEFT`), finishing what can be finished, minimal diffs, committing on green and never pushing
unasked, reviews against the acceptance criteria, runbook answers that were checked, secrets never
printed, subagents for exploration, and the decision interview. It names no issue tracker, code
host or terminal tool.

The rules are yours to cut down. Everything outside the two marker lines is never touched, so
rules of your own go there. Inside them, an update replaces the text with the plugin's: `update`
notices a block you edited (it keeps a checksum of what it wrote in the plugin's data directory),
shows the lines that would be lost and asks before overwriting. If a marker line is missing,
doubled or out of order, every command stops without writing and names the lines; repair or delete
them by hand.

`scripts/adopt.py` is also usable on its own: `python3 adopt.py status <plugin-dir> <state-dir> [path]`.
