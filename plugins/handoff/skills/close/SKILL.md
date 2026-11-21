---
name: close
description: Close a session so the next one starts cold - lands green work, sweeps stale docs, writes the pick-up block into the repo's plan doc, drafts the next session's prompt when repo work is left, closes with DONE / FOR ME / OPEN / LEFT and records the close in the story file. Use when the user types /handoff:close, or says "give me the prompt for the next session", "continue in a new session", "is everything written", "we are at 40% context", "I'm going to bed".
argument-hint: [quick | prompt-only | overnight | spawn] [what the next session should do]
---

# /handoff:close

Close this session so a fresh one picks up with nothing lost and nothing worked out twice.

Arguments: `$ARGUMENTS`

The helper script is called as `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" <command> …`
(`handoff.py` below). Pass every value to it as an argument or on stdin; never write its files
with the Write or Edit tool.

## Modes

Any of these words in the arguments, combined freely; the rest of the text is what the next
session should do and goes into its Today section verbatim.

- `quick`: skip the docs sweep (step 3).
- `prompt-only`: print only the next session's prompt, or, when step 5 drafts none, only the one
  line saying what remains. Every other step still runs.
- `overnight`: unattended. Run `handoff.py away --by "<this session's name>"` first (`handoff.py
  self` prints the name; keep the quotes, a title may hold spaces), so the idle coordinators get
  their last call and every session down the chain knows the user is off five minutes later, and
  put its usage line in the close; then follow "Unattended" below.
- `spawn`: after the close, start the next session without the user (step 9): save its prompt
  and launch it through `flowkit-spawn` with Remote Control on: in the story's tmux tab, or in
  the background with `driver=background`. Where it cannot launch, the saved prompt and its launch
  line go to the user instead.

**Unattended** applies with `overnight`, when `handoff.py state` reports `away: since …`, or when
the AskUserQuestion tool is not available to you (a headless run). Decide by those three facts,
never by guessing whether the user is watching. Unattended, never end on a question: take the
recommended option at every fork, mark it "(auto)" in the decision log and in the close, and
never reopen an "(auto)" item the user already reviewed. The close must exist even if the run
failed: a failed run with a clear OPEN is a good handoff, a silent one is not. Unattended, run
`handoff.py restamp` before step 9: the away flag counts for 12 hours from when it took effect, and the
re-stamp (no nudge, no usage line) keeps a chain longer than that unattended until the user
says back; it does nothing once the flag is gone. Unattended, the
story's last close also stops what its sessions started (simulators, local servers) when no
other session still uses them, and ends with what is ready for the user to try in the morning.

## Procedure

1. **Establish state, read-only.** Run `handoff.py state "${CLAUDE_PLUGIN_DATA}" <repo>` for this
   session's repo. For every repo this session touched, with absolute paths and no `cd`:
   `git -C <path> status --porcelain`, the branch, commits not on its upstream
   (`git -C <path> log @{u}.. --oneline`, or "no upstream"), and whether the repo's validation
   (its test, lint or validate command) last ran green. Note other sessions in play on the same
   story.
2. **Land what is landable.** Validation green and uncommitted changes: run validation once more
   if nothing proves it green since the last edit, then commit in the repo's style, one commit
   per logical change. Never push. Validation red: do not commit; say why under OPEN. Do not
   start new work here.
3. **Docs sweep** (skipped with `quick`). Every doc this session touched or made stale: the plan
   doc gets a dated subsection with the decisions taken (each "(auto)" where taken unattended);
   statements that no longer match the code are fixed in the same commit. A docs commit may come
   before the code; it may never contradict it.
4. **Write the pick-up block** from `pickup-template.md` (next to this file) into the repo's
   pick-up doc, replacing the previous block. The doc is the repo's own plan doc:
   - `state` printed `pickup-doc: <path>`: use it.
   - `pickup-doc: unknown` with exactly one candidate that already holds a `WHERE TO PICK UP`
     block: use it and remember it.
   - Otherwise ask once with AskUserQuestion: each candidate, plus a new `HANDOFF.md` at the repo
     root (recommended when there is no candidate). Unattended, take the first candidate, or the
     new `HANDOFF.md`, "(auto)".
   Whenever `state` printed `pickup-doc: unknown`, run `handoff.py pickup-doc
   "${CLAUDE_PLUGIN_DATA}" <repo> <doc>` with the doc you used, so the question never comes again
   for this repo. Commit the doc as in step 2.
5. **Draft the next session's prompt only when repo work remains**, however small or far off.
   No prompt when the work is finished in the repos, or when everything left needs the user's
   own hands (a device, a console, an approval): those are FOR ME items, and no session can be
   launched for them. Then the close says in ONE line what remains and where it is written, or
   that nothing is left. The pick-up block of step 4 is written either way: it, not the prompt,
   is the real handoff. When drafted, fill `prompt-template.md` (next to this file). Name the
   next session `<repo or story>-<slice>`, for example `billing-s3`.
   **The chain's end.** With a `Story:`, the prompt carries the story's `Chain end:` line: what
   the chain's last slice leaves behind (the branch committed, a Draft MR, the MR ready, merged),
   which `handoff.py chain-end <slug>` prints, or `none`. With `none` (the story's first prompt:
   the close that follows plan approval), ask once with AskUserQuestion: the conf value `state`
   printed as `chain-end:` recommended, the other values `/handoff:setup` offers as the
   alternatives, the user's own words taken as they are; unattended, take the conf value
   "(auto)". Write the answer with `handoff.py chain-end <slug> <value or words>` before the
   prompt is filled, so the story file, the pick-up block and every later brief carry one line. A
   planning session that settled it in its interview writes it the same way. The slice you draft
   is the chain's last when no repo work is left after it: say so on its Chain end line, end its
   Today with that work, and let its Stop when close without `spawn`.
   **Command-first.** When the next slice's work starts with a slash command that only the user
   can start (a skill with `disable-model-invocation: true`, which says "type /x" instead of
   running), the saved prompt is that command line alone, for example `/implement-story 1526354`:
   Claude Code runs a first prompt starting with `/` as the user's own command and passes every
   word after the command's name to it as arguments, which a skill may read as answers. The brief
   goes, in full, into the pick-up block's `Brief:` line (draft it before writing step 4's block,
   or amend the block and its commit), with its `Report-to:` when it has one; `flowkit-spawn` tells the new session to read it
   there. Never put brief text after the command. Every other slice is prompted as before.
   **A helper reports back.** A prompt for a helper beside the chain (a review or investigation
   the user asked for, or the ladder's owed review; never the chain's next slice) carries
   `Report-to: <session>` in its header and the send sentence of its Stop when, when `state`
   printed `report-to: on` (this session's name, which `handoff.py self` prints) or a name (that
   session); with `report-to: off`, neither.
6. **The ladder step**, only when `state` printed `ladder: <path>`. Read that file and follow it:
   its "Rung" section says how to choose Model, Effort and the Why line for the next session
   from its kind (planning, investigation, implementation, review, docs or config); its
   "Close step" section is what to do at every close, before step 10 (log a row, grade the
   previous session, launch a review it owes). It may name its own scripts; run them as it says.
   With `ladder: none`, skip this step and leave the Model, Effort and Why lines out of the
   prompt: the next session runs at the user's defaults. A model or effort the user named in
   this session wins over the ladder; say so in the Why line.
7. **Close** with these four blocks, each line short and plain:
   - `DONE`: one line per item. Per repo touched: branch, committed yes/no, pushed yes/no, and
     where they apply: how it was verified, the pipeline result, links to the change request
     and the story or ticket, changes outside the repo, anything left running. A story that
     spans sessions opens with one line: code left · shipped · safe to close this session:
     yes/no.
   - `FOR ME`: numbered, only steps that need the user's hands. A step that unblocks more work
     names what follows it. "Nothing" when nothing needs them.
   - `OPEN`: what is blocked, and on what.
   - `LEFT`: what is left on the story, or "nothing left".
   Then the prompt in a fenced block when step 5 drafted one, otherwise its one line. With
   `prompt-only`, print only that block or that line.
8. **Peers.** If another session needs to know (one waiting on a contract, a coordinator), put
   the message to send under OPEN. Do not send it unless asked, or unless the prompt this
   session started from (started from a command: its brief) carries a `Report-to:` line naming
   a session: then send the verdict as
   that line says (SendMessage, loaded with ToolSearch: one line, findings by number with a
   worth-fixing word, the links, at most ten lines), and on any error or delivery notice (held
   for the receiver's approval, not reachable) say so under OPEN; DONE carries the verdict
   either way.
9. **With `spawn`**, when step 5 drafted a prompt. Spawn needs a clean tree (step 2 committed
   everything green); a dirty tree goes under OPEN and nothing launches. Never save two prompts
   for one slice.
   1. Save the prompt: `handoff.py save-prompt <next name>`, the prompt on stdin. It prints the
      saved path. The launch line, for the user and the story record, is
      `cd <repo> && claude -n <next name> [--model <model>] [--effort <effort>] "$(cat <saved path>)"`.
   2. Launch it, absolute paths, no `cd`:
      ```sh
      "${CLAUDE_PLUGIN_ROOT}/bin/flowkit-spawn" --repo <repo> --name <next name> \
        [--story <slug>] [--model <model>] [--effort <effort>] <saved path>
      ```
      `--story` is this session's `Story:` slug (step 10); leave it out when there is none. Pass
      the model and effort of the prompt as they are, `max` included; leave out what the prompt
      leaves out. The launch driver is `spawn-driver` in `state` (set in
      `~/.config/flowkit/handoff.conf`); `flowkit-spawn` reads it itself. Run it with a
      5-minute Bash timeout: two launches (the reporter, then the session), each typed once
      more when claude exits at start-up, and the wait for the Remote Control link add up.
      When `~/.claude/load-hold` exists the launch waits until it is gone, which can outlast any
      timeout: run it with `run_in_background: true` instead and read its notification; DONE
      says the launch was held and why (its stderr).
   3. Read its last line and exit code:
      - `SPAWNED pane=<n> … tab=<tab> … rc=<url|pending> … try=<1|2>`: started. DONE gets one
        line: the session's name, its tab and slot, its Remote Control link; with `try=2`
        (claude exited at start-up once, stderr said `warning: session '<name>' exited at
        start-up in <pane>; typed again`, and the second launch came up) that line adds
        "launched at the second try". A `REPORTER` line before it means the story had no
        `<slug>-report` running and one was started (slot 1 of a new tab, else a pane of its
        own; its slot is in the line). Step 10 writes `Next: started <name>`. A `warning: no
        session … came up` before it means the command was typed but the session did not
        confirm within 15 s, most often a folder-trust prompt waiting for a yes: still
        `started`, plus a FOR ME step to look at that tab and slot. `parked=<line>` at the
        line's end (stderr: `warning: session '<name>' is parked at a start-up prompt …`) means
        the pane sits at the start-up prompt that begins with that line: still `started`, and
        the FOR ME step names the prompt, the tab and the slot; never answer it yourself, what
        it asks is the user's to decide. Before anything else after
        that warning, look at `claude agents --json` and the pane
        (`tmux capture-pane -p -t <id>`): the session is usually there, starting or at a prompt.
        A pane at a shell may be relaunched through `flowkit-spawn` again without `--pane`
        (refusal 2 guards against a namesake); a pane at a prompt is FOR ME; never a launch
        typed by hand: a window opened by hand carries no story mark, so it gets no labels, no
        autoclose and no reporter, and the chain runs unreported.
      - exit 10, stderr `session '<name>' exited twice at start-up in pane <id>; its output is
        in the pane`: the launch was typed twice and claude exited both times; nothing runs in
        that pane (a `REPORTER` line before it means the reporter came up and runs). Say so
        under OPEN, put the launch line under FOR ME naming the tab and slot ("tab <tab> slot
        <n> is at a shell: claude exited twice, its output is in the pane"), and step 10 writes
        `Next: pending <name>`.
      - `SPAWNED driver=background id=<id> … rc=<url|pending>`: started in the background.
        DONE gets one line: the session's name, `claude attach <name>` to open it, its Remote
        Control link. A `REPORTER driver=background` line before it means the story's reporter
        was started too. Step 10 writes `Next: started <name>`.
      - exit 11, `QUEUED #<n>, ~<HH:MM> · id=<id> · <why>`: the capacity gate queued the
        launch (the machine's slots are all used by other chains, or a resource in its `Needs:`
        is held); it starts by itself when a slot or the resource frees, and this close's own
        record is one of the moments that checks. Nothing is held for it meanwhile. DONE gets
        one line: the session's name, `queued #<n>, starts ~<HH:MM>`, and why. Step 10 writes
        `Next: queued <name>`. Never relaunch with `--over-cap` unless the user says "over cap".
      - exit 3 (not inside tmux), 4 (a session of that name runs), 6 (uncommitted changes),
        7 (driver not available), 8 (the folder was never trusted: FOR ME is to run `claude` in
        the repo once and accept the prompt, then the launch line) or 9 (`claude --bg` failed,
        its output printed): nothing launched. Say why under OPEN, put the launch line
        under FOR ME as the step that starts the next session, and step 10 writes
        `Next: pending <name>`. Never retry with `--force`: a running namesake or a dirty tree
        means two sessions would share one working tree.
   After a successful spawn this session touches the repo no more: no edits, no commits; the
   next session owns the working tree.

9a. **Register the watch**, on every close of a chain's last slice that pushed, with or without
    `spawn`, before step 10. The slice is the last when its brief's `Chain end:` line says so,
    and it pushed when that line has the slices push (`draft-mr`, `mr-ready`, `merge`, or the
    user's words to that effect); with `branch`, or nothing pushed, there is no watch. Nobody
    waits for the pipeline: a script polls it, so this session's slot, its leases and its tree
    are free at the close. With a `Story:`, from the tree that was pushed:
    ```sh
    "${CLAUDE_PLUGIN_ROOT}/bin/flowkit-watch" add --story <slug> --mr <merge request URL> \
      [--ready] [--until merged]
    ```
    - `draft-mr`: neither flag; the MR stays in Draft.
    - `mr-ready`: `--ready`; the watch takes the MR out of Draft on green, so the slice leaves
      it in Draft.
    - `merge`: `--until merged`; the slice took the MR out of Draft and set auto-merge at its
      push, and the watch reports the merge and the target branch's pipeline.
    - The user's own words: the flags that carry them out; what no flag covers stays a FOR ME
      step.
    It arms what was pushed (`@{u}`), not `HEAD`, so the commits of steps 2 to 4 change nothing,
    and it asks the forge nothing, so the close never waits for the network. A slice that pushed
    in two repos registers one watch per merge request, each from its own tree. A push with no
    merge request is watched as `--ref <branch>`; a pipeline that is not GitLab's as
    `--check '<command>' --name <name>` (exit 0 green, 1 red, 2 still running). A fix slice a
    watch started registers the next watch the same way, with no flags: it keeps those of the
    watch before it.
    Its last line is `watching <name> · sha <sha> · until <green|merged>`: step 10 writes
    `Next: watching <name> (<merge request URL>)`, and DONE gets one line, "pipeline watched as
    <name>". The pipeline goes under neither FOR ME, OPEN nor LEFT: the watch owns it, and writes
    its own section under `<name>` when the pipeline ends, with this close's LEFT in it. Green
    is `Next: none`; the first red starts one fix slice in the story, marked "(auto)"; a second
    red, a pipeline that never starts and a broken watch are reported to the user there. A merge
    (other than `merge`'s own auto-merge), a release and a message to a person still wait for
    the user's word.
    When `add` fails (exit 1, the reason on stderr: no `glab` on the PATH, not a merge request's
    URL, no upstream): say why under OPEN, put the pipeline's link under FOR ME as the step to
    look at, and step 10 writes `Next: none`. Never wait for the pipeline in its place.
10. **Record the close in the story file**, as the last tool call before the reply. The story's
    slug is the `Story:` of the prompt this session started from (started from a command: of the
    brief in the pick-up block); with none, skip this step. Pipe
    the record into `handoff.py record <slug>`:
    ```
    ## <this session's name> · <date "+%Y-%m-%d %H:%M">
    Session: ${CLAUDE_SESSION_ID} · Repo: <path>
    Resume: cd <path> && claude -r ${CLAUDE_SESSION_ID}
    Next: started <name> | queued <name> | pending <name> | watching <name> (<link>) | none
    [Prompt: <saved path> — launch: <launch line>]
    <the DONE / FOR ME / OPEN / LEFT of step 7, verbatim>
    ```
    This session's name is the `Session:` of its prompt; with none, `<repo>-<branch>`. `Next` says
    what follows:
    - `started <name>` when a session was launched;
    - `queued <name>` when the capacity gate queued it (exit 11): it starts by itself;
    - `pending <name>` when step 5 drafted a prompt and nothing launched it. The prompt must be
      saved: if step 9 did not save it, run `handoff.py save-prompt <name>` now, and add the
      `Prompt:` line with the path it printed and the launch line of step 9.1;
    - `watching <name> (<link>)` when step 9a registered a watch: the name `flowkit-watch add`
      printed, several joined by `, ` before the one bracket. The story is not done until each
      has its own section. A close that also started a session writes that `Next:` line first
      and this one under it;
    - `none` only when step 5 drafted nothing and step 9a registered no watch: no repo work is left on the story. `Next: none` on the last close (a watch's own section is one), with none of the story's sessions still live,
    is what marks the story done. `handoff.py record` also types `closed <this session's name>`
    into the pane of the story's live `<slug>-report` session, so do not message it yourself;
    without a reporter, the file is the record. Its second line says what that doorbell did:
    `reporter: rung`, `background` (woken), `waiting` (at a prompt, not rung) or `none` (no
    reporter runs). `waiting` or `none` goes into the reply's DONE as one line, so a story
    running without its reporter is seen; never start one by hand (the next spawn does).
