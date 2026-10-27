The next session's prompt. The close fills it in and prints it in a fenced block, or saves it
for a launch. Lines in [brackets] appear only when they apply. Name sections to read, never
whole files; carry what this session verified so the next one does not look it up again. The
Chain end line is the story's own (`handoff.py chain-end <slug>`), repeated by every brief.
`Report-to:` names the session a helper beside the chain sends its verdict to (step 5), never a
chain successor.

```
Repo: <absolute path>   Session: <next session's name>[   Story: <slug>][   Report-to: <session>]
[Model: <model>   Effort: <effort>]
[Why this model: <the reason the ladder gave>]

Read first, ONLY these sections: <doc → section>, <doc → section>. Don't read the
whole files; don't re-derive what the docs already carry.

Branch from: <branch>. Validation: <command> must be green before any commit;
commit per change; push only as the Chain end line says (no line: never push).

Verified facts: <bullets>

What the docs do not say: <bullets: what exists only in this session's head>

Today: <the slice, in order, with the check that proves each item done>

Don't: <scope fences: files or areas not to touch, things already decided>

Keep Today's items as a checklist in your replies and tick them as you go; check it before
ending any turn. A text-only message is a progress report, not the end: while an item is
open, keep working or say what blocks it.

[Chain end: <the story's line>[; this slice is the chain's LAST: it does that work, then closes without `spawn`]]
Stop when: <done condition>, or when a step needs the user's hands; then close with
`/handoff:close` (add `spawn` when the plan runs on without the user), which records the
close in the story file[; then send your verdict to <Report-to> with SendMessage (load it
with ToolSearch): one line, findings by number with a worth-fixing word, the links, at most
ten lines; on any error or delivery notice, DONE carries it].
```
