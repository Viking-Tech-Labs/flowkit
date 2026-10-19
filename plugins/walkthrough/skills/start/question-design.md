# Question design for a walkthrough

The quiz is only as good as its questions. The examples below are invented; take the shape, not
the content. Run history is kept by `scripts/history.py` and is never read while writing
questions: a second walkthrough of the same code must not find its own answers.

## What to ask about

- **A moment, not a symbol.** Every question starts from something a user does or a concrete
  moment in the product's life: "a user saves the form while offline", "the app starts cold a day
  after sign-in", "a visitor who is not signed in opens a shared link". Never "what does this
  function return".
- **Set the scene in the question.** Two to four sentences: who acts, on which version, release
  or state, what happened just before, and exactly what is asked. The user answers cold, often
  days after the change was planned, and must never have to rebuild the setup from earlier
  rounds. The scene is context, not a hint: it never narrows toward the answer.
- **Mechanisms and sequencing.** Which layer decides, in what order, who is affected. Names,
  paths and counts are trivia; skip them.
- **The reasonable expectation is always an option** when the code deviates from it. That option
  is how disagreements surface, and a disagreement the claims back becomes a proposed change.
- **Never the sentence just read.** A question after a layer tests a consequence the layer text
  did not spell out, or the text withholds the mechanism the question will test. An answer that
  contradicts text the user just read cannot be graded: it is either a sound expectation or a
  lapse, and the three colours do not tell them apart.

## Options

- Three or four. Labels of four words or fewer; each description states the complete outcome in
  one or two sentences, never a fragment the user has to expand.
- Exactly one correct option, verified against the code before the batch goes out, with the
  `file:line` in your notes. Never from memory of your own summary.
- Each wrong option encodes one specific misconception a programmer who has not read the code
  could hold. The grading names the misconception, so vague distractors waste the round.
- No giveaways: option text does not quote the explanation, the correct option is not the
  longest, and its position varies across the batch.

## Batches

- At most four questions, themed; a header of twelve characters or fewer.
- Round 0 (before any explanation): one question per journey, at most four journeys.
- Layer rounds: one or two questions on the layer just explained. A re-test question may share
  the batch with the next layer's questions.
- Re-test rounds: only the missed concepts, each from a new angle:
  - the mirror case (off-then-on after on-then-off),
  - a boundary (zero items, offline, a pending request, the default configuration),
  - another actor (a visitor, a second account on the same device, after sign-out).
- About fifteen questions in a walkthrough. Past twenty, the layers were too thin.

## The disagreement follow-up

A disagreement says the user's expectation is sound. It says nothing about whether they hold what
the code does instead. The next batch carries one follow-up on it, always of the shape "the code
does X; given that, what happens when …", on a scenario the disagreement did not name:

- disagreement: "the draft is saved when the server confirms" (the code saves it at the tap)
- follow-up: "the code saves it at the tap; the request then fails: what does the list show?"

Right: the concept counts as understood and the proposed change can be written. Wrong: a miss,
remediated like any other.

## Grading language

- Correct: "Correct: `path:line`."
- Miss: "Miss: the code decides this at `path:line`. Your option would hold if …" and name the
  misconception in one clause.
- Disagreement: "Your answer is what the code should do. It does X instead (`path:line`); that
  is finding N." Not counted against the score.

## Representations for remediation

Pick the one that matches the miss pattern:

| misses look like | use |
|---|---|
| rules held, order missed | a timeline table: the moment, what the rule reads, the state on each side |
| layers confused | a table with one line per layer: who sets it, and when |
| asynchronous order | a numbered call trace with the counter or token value per call |
| who is affected | an actor table: visitor, signed in, mid-change, after sign-out |

End the remediation with the two or three rules that cover every row. Each rule states its
scope ("while signed in", "for a saved draft") and is checked against the layers not yet
explained: a rule that is true for every row shown and false one layer up is the next miss.
Then re-test from those rules.

## The unattended form

When nobody can answer, the questions still get written, without their answers, and the answer
key goes under its own heading at the end. The steps to try by hand carry the weight instead:

- One step is one action with one thing to look for. "Apply the code twice; the total drops
  once" is a step. "Test the discount logic" is not.
- Order them as the data flows, so an early step that fails explains the later ones.
- Each names its starting state (signed in or not, empty or filled, first run or second) and the
  `file:line` that decides what the reader will see.
- Where the read found a deviation from the claims, the step says both what the claims lead you
  to expect and what the code will do.
