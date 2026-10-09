---
name: start
description: Make the user understand a change by predicting it before reading it - a ten-line summary and the user journeys, then scenario questions on what happens underneath, each answer graded as correct, a miss (their model of the code is wrong) or a disagreement (their expectation is sound and the code deviates, which is a finding), the explanation one layer at a time with a re-test, and every disagreement the stated claims back written up as a small, rejectable change. Takes a branch, a pull request number or URL, or a pasted diff. Use when the user types /walkthrough:start.
argument-hint: <branch | pull request number or URL | "pasted"> [claims <text or file>] [backend <path>] [review] [unattended]
disable-model-invocation: true
---

Reading an explanation produces recall. Predicting a behaviour and being told where the code disagrees produces understanding, and the places where a sound expectation and the code disagree are findings. So the order is: summary, predict, grade, explain a layer, re-test, adapt, then the changes worth proposing. Never the explanation first.

Arguments: `$ARGUMENTS`

Read them as: the change (a branch name, a pull request number or URL, or the word `pasted` when the diff is in the conversation or about to be); optionally `claims <text or file>` (what the change is supposed to do); `backend <path>` (a checkout of the other side of the project's API, read for this run and remembered for the project); the word `review`, which adds a code review (step 5); and the word `unattended`, which gives the form for a run nobody will answer (step 1). With no change named, ask for it in one line.

Read `${CLAUDE_PLUGIN_ROOT}/skills/start/question-design.md` before writing the first question.

## 0. Pick the slice and read it yourself

Say in one line that you are reading, then read. The summary depends on the read and arrives some minutes later, and silence until then looks like a hang.

Run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/history.py" where "${CLAUDE_PLUGIN_DATA}" .` first. It prints where the run will be recorded and whether a backend checkout is set for this project. Do not open the history page it names: it holds earlier scores, and nothing in it helps the read. If the user gave `backend <path>`, run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/history.py" backend "${CLAUDE_PLUGIN_DATA}" . <path>` before reading; it remembers the path for this project.

**The change.**

- A branch: the range is `merge-base(<default branch>, <branch>)..<branch>`, read with `git diff` and `git log`.
- A pull request: its base branch, its description and its diff, through the code host's command line when one is installed and signed in (`gh`, `glab`); otherwise fetch the head with git, or ask the user to paste the diff. Do not read the pull request's comments or review threads: predictions come from the user's reasoning, not from what others said.
- A pasted diff: the diff is the range. Find the files it touches in this checkout to read around it; if they are not here, work from the diff alone and say so.

Never switch the user's checkout. When the head is not what is checked out, read it with `git show <head>:<path>` and `git grep <pattern> <head>`, or add a git worktree under `${CLAUDE_PLUGIN_DATA}/worktrees/` when the read is long; name the worktree at the close and leave it until the user says, since the `file:line` references resolve there.

**The claims.** What the change is supposed to do is what the code gets measured against in step 5. Take them, in this order, from: the `claims` argument; the pull request's description; a ticket the description links, read through whatever tracker skill or connector this session already has; the commit messages. Quote the sentences you will use. With none of these, say that the claims are missing and that every proposed change will be marked "claims silent".

**Around the diff.** The diff is the entry, not the slice. For each mechanism the change touches, read the code that writes the state and the code that reads it, in the changed files and their callers, so that no re-test needs a fresh read between rounds. The answer often lies outside the project:

- The other side of an API, when what a client shows is decided by a server rule or the reverse. Read it only when `where` printed a backend checkout; note the branch and commit it printed, because that checkout may not be on the matching branch. With none set, work from the project alone, say at the close which answers rest on a contract you could not read, and mention once that `backend <path>` sets it.
- A library default: the installed dependency in this checkout, at its installed version.
- Generated code (a client, a schema): read it, and treat it as the contract.

Read until you can answer every question you are about to ask with a `file:line`. The read is yours: a subagent may find callers or run the tests, but whoever writes the answer key has read the code. Note each mechanism and the line that decides it. That list is the answer key and the layer list for step 3.

## 1. Summary, then predict

First output, ten lines at most: two or three sentences on what the change does for the person using the product, then the journeys it touches, at most four, each "the user does X". For a library or a service, a journey is a caller doing something. No mechanisms, no findings, no file names.

Then round 0: one question per journey, "what happens underneath", with options that are plausible mechanisms, including the one a reasonable programmer would expect. The user answers from reasoning, not from an explanation they have not had yet. Check the answer key against the code before the batch goes out.

Ask with the AskUserQuestion tool, and only with it. Before round 0, check that the tool is in this session's tool list.

**When nobody can answer.** If the AskUserQuestion tool is not available, or the arguments hold the word `unattended`, nobody is there to answer: this is a headless or scheduled run, and a question printed as text would end the run with nothing delivered. Do not print round 0 and stop. A walkthrough needs a person, so say in the first lines that you could not ask and that this is the unattended form. Then produce, in one reply and in this order:

1. The summary and the journeys, as above.
2. **Steps to try by hand**, numbered in the order the data flows, one or more per journey. Each step: what to do (the action, the input, the state to start from), what to look for, and the `file:line` that decides the outcome. Include the boundary cases the read turned up (empty, repeated, out of order, another kind of user).
3. **Questions to answer first**: the round-0 batch and one question per layer, written as in an attended run, without their answers.
4. **Answer key**, under its own heading at the end so the reader can predict before reading it: each answer with its `file:line` and the misconception each wrong option encodes.
5. The findings from the read (step 5), numbered, with a worth-fixing verdict each.

Then record the run as not asked (step 6, the `record` command; it is part of this form, not optional) and end by saying that the same command in an interactive session gives the quiz. Skip steps 2 to 4.

## 2. Grade in three colours

Every answer gets one of three verdicts, each with the line that decides it:

- **Correct**: one line confirming the mechanism.
- **Miss**: the user's model of the code is wrong. Name the misconception their option encodes, say what the code does and where, and what would have to be true for their option to hold ("your answer describes what would happen without that check, which is why it is there").
- **Disagreement**: their option is what the code *should* do and the code deviates. Say so in those words, number it as a finding candidate, and do not count it against them. A disagreement can hide a miss: a sound expectation says nothing about whether they hold what the code does instead. So it is not understood until one follow-up lands, in the next batch, of the form "the code does X; given that, what happens when …". A missed follow-up is a miss and goes through step 4 like any other.

The score is correct over correct plus misses. Disagreements go to step 5.

## 3. Explain one layer, re-test it, next layer

Walk the layers in the order the data flows (configuration, startup, stored state and cache, the rule, the interface, the contract with the other side, or whatever this slice has). Merge thin layers: two that would each take one question are one layer. Each layer: eight lines at most, every mechanism with its `file:line`, then one or two questions on that layer before the next. A layer's question never tests a mechanism its own text just stated. It tests a consequence the text did not spell out, or the text withholds that mechanism for the question. About fifteen questions for a whole walkthrough; twenty is too many.

Wrong options keep encoding one specific misconception each, and the grading names it.

## 4. Adapt until it lands

After each round name the pattern in the misses: rules held but sequencing missed, layers confused, asynchronous order, who versus when. Change the representation to fit the pattern: a timeline table, a call trace, a state table, one user's day. Close the remediation with the two or three rules that cover every row, and give each rule its scope ("while signed in …", "for a saved draft …"): a rule worded universally that covers only the rows shown sends the next answer into a miss on the rows it did not. Check each rule against the layers not yet explained. Then re-test only the missed concepts, each from a new angle: the mirror case, a boundary, another actor.

Stop quizzing a concept when it has been answered right once from a second angle, or after two rounds with no progress. At that point stop explaining too: name the check by hand that shows it (run it offline, flip the setting, start it cold, call the endpoint twice), list it under "Check by hand" at the close, and move on. Seeing it beats a third explanation.

## 5. Findings and proposed changes

**Findings.** The disagreements plus whatever the read turned up, numbered, one worth-fixing verdict each. Nothing is posted anywhere. With `review`: also run a code review of the same range (the project's own review skill when it has one, else `/code-review`), reconcile its findings with yours, and let the later rounds quiz the reconciled findings as scenarios, since a finding the user can predict is one they can defend to the author. Only with `review` are the pull request's existing comments read, to mark duplicates.

**Proposed changes.** For each disagreement, after its follow-up has landed, check it against the claims from step 0:

- The user's expectation and the claims agree, the code deviates: write the proposed change, the smallest one that makes the code match. It carries the files, the rough size (a condition, a function, a new endpoint), the blast radius (every other caller of the mechanism, named, since a shared function or filter moves with it), where it belongs (this change, a follow-up, the other side of the API, a product decision), and the trade-off (what the current behaviour buys, which the change gives up).
- The user's expectation and the claims disagree: no proposed change. Say which sentence of the claims the code follows, and whether the question is worth raising with whoever owns the product decision. An instinct against the stated intent is a product note, not a code change.
- The claims are silent: write the proposed change and mark it "claims silent". The author decides.

Proposed changes are a numbered list, rejectable by number. Nothing is applied and nothing is posted until the user says which. One that the user wants sent to the author is phrased as a suggestion the author can decline, with the trade-off in it.

## 6. Close and record

- **Where your understanding stands**: solid (right first time), landed late (missed, then right from a second angle, each with one line of the mechanism), not landed (with the check by hand that shows it), not quizzed.
- **Findings**, numbered, disagreements marked as such. **Proposed changes**, numbered, each with its size, blast radius and home.
- **Check by hand**: the checks from step 4, each with what to look for.
- **Read outside the project**: the backend checkout with its branch and commit, the libraries with their versions; or which answers rest on a contract that could not be read. The worktree, if one was added.
- **Skill friction**: what in this skill or its question guide was unclear, missing or wrong in practice, in two or three sentences. Leave it out when there was none; that a run was unattended is not friction.

Then record the run with one command, quoting each value for the shell:

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/history.py" record "${CLAUDE_PLUGIN_DATA}" --id <date>-<project>-<target, as lowercase words joined by dashes> --date <YYYY-MM-DD> --project <the repository's name> --target "<branch x | PR 12 | pasted diff>" --questions <asked> --correct <n> --misses <n> --disagreements <n> --pattern "<the miss pattern in a few words>" --friction "<the friction sentences>"`

Leave `--friction` out when there was none. A run nobody could answer has `--questions 0` and no pattern.

It writes one row and the friction bullet to the history page, in the user's brain vault when they have one and in the plugin's data directory otherwise, and prints which. Never edit that page's rows by hand, and never write questions or answers into it: a later walkthrough of the same code must not find its own answer key. In a vault that is a git repository the script also commits the files it wrote, those only; never push them. If it prints that git refused, pass that on.

A fork in what the slice should cover is a question for the user before the read goes on, asked with its options and your recommendation. A verdict on a finding ("raise it with the product owner, no code change") is a verdict, not a question.
