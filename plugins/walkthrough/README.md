# walkthrough

Makes you understand a change by predicting it before you read it. Claude reads the branch, the
pull request or the diff, gives you ten lines on what it does and the user journeys it touches,
and then asks what happens underneath each one, before explaining anything. Where your answer
and the code differ, either your picture of the code was wrong, or the code is: the second kind
is a finding.

Install, after adding the flowkit marketplace (see the root README): `claude plugin install walkthrough@flowkit`.

| Skill | What it does |
|---|---|
| `/walkthrough:start <branch>` | Walks the commits on that branch since it left the default branch. |
| `/walkthrough:start <number or URL>` | Walks a pull request, read through your code host's command line (`gh`, `glab`) or, without one, from a diff you paste. |
| `/walkthrough:start pasted` | Walks a diff you paste into the conversation. |
| `… claims <text or file>` | What the change is supposed to do, to measure the code against. Without it: the pull request's description, a ticket it links (read by whatever tracker skill or connector your session already has), then the commit messages. |
| `… backend <path>` | A checkout of the other side of the project's API, read (never changed) when the answer lies there. Remembered for the project. |
| `… review` | Also runs a code review of the same range and quizzes you on its findings. |
| `… unattended` | The form for a run nobody answers: steps to try by hand, the questions, an answer key. Chosen on its own when the session cannot ask. |

It runs only when you type it. No hook, nothing at session start.

## How a walkthrough goes

1. **Summary, then predict.** Ten lines and up to four journeys ("the user does X"), then one
   question per journey with three or four plausible mechanisms to pick from. You answer from
   reasoning.
2. **Three verdicts.** *Correct*. A *miss*: your model of the code is wrong, and you get the line
   that decides it and the misconception your option encodes. A *disagreement*: your answer is
   what the code should do and it does something else. A disagreement costs you no point and
   becomes a finding, after one follow-up question that checks you also hold what the code does
   instead.
3. **One layer at a time.** Eight lines of explanation, one or two questions on a consequence the
   text did not spell out, next layer. About fifteen questions in all.
4. **Misses change the picture.** The pattern in your misses is named (order, layers, who versus
   when), the explanation switches form (a timeline, a call trace, an actor table), and only the
   missed concepts come back, each from a new angle. Two rounds without progress end in a check
   you run by hand, not a third explanation.
5. **Proposed changes.** Each disagreement that the stated claims back is written up as the
   smallest change that makes the code match: files, size, what else it touches, where it
   belongs, what it gives up. A numbered list you accept or reject by number. Nothing is applied
   or posted until you say which.

The close says where your understanding stands (solid, landed late, not landed, not quizzed), the
findings, the proposed changes, the checks to run by hand, and what was read outside the project.

## Without you

A walkthrough needs a person. In a run nobody can answer (headless, scheduled, or any session
without the question tool) the skill says so and gives the unattended form: the summary and journeys, numbered steps to try by hand with what
to look for and the line that decides each, the questions without their answers, an answer key
at the end, and the findings.

## Where things go

- **Run history** is one page: a row per walkthrough (date, project, target, questions, correct /
  miss / disagreement, the miss pattern) and a "Friction" list of what in the skill itself was
  unclear. With the `brain` plugin's vault it is `wiki/walkthrough-runs.md` there, added to the
  vault's index and log when first created and committed there (only the files the run wrote,
  never pushed); the vault is found through `BRAIN_DIR` or the path
  `/brain:init` stored. Without a vault it is `runs.md` in this plugin's data directory. Nothing
  needs setting up, and the `brain` plugin is not required. Over months the page shows which kind
  of mechanism you tend to misjudge.
- **Only scores, never answers.** The page holds no question and no answer, so walking the same
  code again cannot read its own answer key.
- **The backend path** is kept per project in `backends.json` in the data directory. Clear it
  with the script below.
- **Your checkout is not switched.** A head that is not checked out is read with `git show`, or
  in a git worktree under the data directory that is named at the close and left until you say.

## Your tracker and code host

The plugin names neither. A pull request is read through whatever command line you have
installed and signed in, and a ticket through whatever skill or connector your session has. With
neither, paste the diff and the claims.

## The script on its own

Python 3, standard library only.

```
python3 scripts/history.py where <data-dir> [project-dir]
python3 scripts/history.py record <data-dir> --id ID --date YYYY-MM-DD --project NAME --target TEXT [--questions N --correct N --misses N --disagreements N] [--pattern TEXT] [--friction TEXT]
python3 scripts/history.py runs <data-dir>
python3 scripts/history.py backend <data-dir> <project-dir> [<path> | --clear]
```
