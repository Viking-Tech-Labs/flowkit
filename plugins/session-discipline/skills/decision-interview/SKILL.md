---
name: decision-interview
description: Get a decision or a missing fact from the user as an interview - investigate first, list every open question, give each a recommended answer and its alternatives, ask them as choices the user can pick, and record what was agreed. Use before a choice that is expensive to reverse (a storage key, an API or wire contract, a schema, a product direction), when different readings of a request would lead to materially different work, when a fact only the user has blocks the work, or whenever a reply would otherwise end in a question to the user or a list of options for them to choose among. Also when the user says "interview me", "ask me what you need", "give me options with a recommendation".
---

A decision interview replaces two habits that fail the same way: the bare question ("what do you want here?") and the silent assumption. After either, neither side knows what was agreed. After an interview both can point at a numbered list.

## When it applies

- A choice that is expensive to reverse, or a request that different readings would turn into materially different work.
- A fact the user has and you do not: where a credential lives, which environment a consumer reads from, a list of IDs. An unknown fact blocks the work as hard as an unmade decision. Ask it the same way, with the candidate answers listed and "I'll go find out" as a standing option.
- Any reply that would end by asking the user something, and any list of options, fixes or improvements you would hand them to choose among. That content becomes an interview before the reply is sent. This check needs no judgment about how reversible the matter is. Review findings are the exception: they stay a numbered list the user fixes by number.
- In the middle of the work, not only at design time. The likely miss is a fork that surfaces a few commits into an implementation, when it looks small enough to settle in a sentence.

It does not apply to routine judgment calls. Make the reasonable call, state it as an assumption in your final message, and move on. Scale the interview to the fork: one question with a proposal is a valid interview; a full numbered enumeration is for a design session.

## The protocol

1. **Investigate first.** Ground every question in the actual code, documents and precedent before asking. The user should never have to look something up in order to answer you.
2. **Enumerate the whole decision space up front**: a numbered, themed list of every open question, so the user sees all of them before answering any.
3. **Every question carries a proposal.** The recommended answer comes first, marked "(Recommended)", with one or two lines of why, then the credible alternatives with their trade-offs. "What do you want?" is not a question.
4. **Ask as choices.** Use the AskUserQuestion tool when it is available, in themed batches of at most four questions, so the user picks an option or writes their own. Without that tool, write the same thing as a numbered list with lettered options. Read answers literally: a custom answer, or a question back, is not a selection. Clarify, refine the options and ask again. "I misclicked" means asking the same batch again.
5. **When the user picks against the recommendation, confirm once**, with the consequences spelled out in plain words. Confirmed twice, it is settled: record it as a deviation with their reason and never reopen it.
6. **Propagate.** When an answer invalidates earlier answers, name exactly which ones and ask only those again.
7. **Loop until nothing is open.** Forks that the answers bring up get a fresh round.
8. **Write it down.** The result is a numbered decision log (the choice, the reason, deviations, corrections to the source documents) kept where the project keeps its documents, and committed. The agreement exists once it is recorded.

## When the user is not reachable

In a scheduled, background or otherwise unattended run, do not block on a question nobody will answer. Take the recommended option, record it in the decision log as an assumption marked "(auto)", and flag it in your final message so the user can overturn it. Never reopen an "(auto)" item the user has already reviewed.

## Two companions

**Attack the recommendation before presenting it.** Before you recommend something that freezes a choice, try to refute it: what breaks, what it costs later, what it assumes that might be false. Present the findings that survive together with the proposal, rated by severity. This is for recommendations that freeze something, not for every small suggestion.

**Explain before recording.** When a decision rests on a concept the user may not fully hold, offer a plain explanation of how it works, what it buys and what it costs, before anything is written down. A question back in the middle of the interview is the signal to do this. A batch answered entirely with the recommended options is not proof of understanding: before recording a decision that builds a mechanism (a hook, a script, an automated flow), say in three lines how it runs: what triggers it, what the user sees, what the user does. If the explanation changes their view, confirm the affected answers again.
