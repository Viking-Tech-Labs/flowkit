---
name: edit
description: Change the fixed model ladder - a kind's model or effort, or turning effort max on or off (which arms the effort gate). Shows the change as a diff and writes it only on a yes. Use when the user types /ladder:edit, or asks to run a kind of session on another model or effort, or to allow max.
---

Change the table `/ladder:setup` wrote. The helper is
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ladder.py"` (`ladder.py` below).

1. Run `ladder.py show`. With `ladder: none`, say the table is not set up and point to
   `/ladder:setup`; stop. With `managed: no`, the file is the user's own: it stays as it is, and
   this skill does not edit it; stop.
2. Work out the change from what the user asked, as arguments: `<kind>=<model>/<effort>` per
   row (kinds planning, investigation, implementation, review, docs, config; efforts low, medium,
   high, xhigh, max; a model alias such as `sonnet` or `opus`, or a full model id), and `max=on`
   or `max=off`. A row at effort `max` needs `max=on` in the same change. If the ask is unclear,
   show the table and ask with AskUserQuestion which rows change, a proposal first.
3. Run `ladder.py diff <arguments>` and show the diff. When it turns `max` on, say what that
   arms: a launch at `--effort max` will ask for the user's yes, and runs at `xhigh` while they
   are away.
4. Ask with AskUserQuestion: write it (recommended), or leave it. Without the AskUserQuestion
   tool, write nothing: print the diff and the command that would write it, and stop.
5. On a yes, run `ladder.py write <arguments>` and relay its line. The next close uses it.
