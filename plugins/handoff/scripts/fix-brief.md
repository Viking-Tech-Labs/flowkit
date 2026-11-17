The brief of a slice a watch starts by itself (watch.py): the fix slice of a merge request's first
red, or the slice that merges a base in once that base is green again. A `=== <name>` line starts
a block, `{word}` is filled in; the prompt is head, then red or base, one of tree, worktree and
checkout, merge when the chain ends with one, then tail. The header keeps the shape of
skills/close/prompt-template.md.

=== head
Repo: {tree}   Session: {session}   Story: {story}
{model}Launched-by: watch {arm}, registered by {by}
Needs: none
Left: 0

A watch started you: nobody approved this launch, so mark what you decide "(auto)". No close
drafted this prompt: where `/handoff:close` finds a ladder, this session has no row yet.

=== red
{url} is red at {sha}: {what} ({link}). You are the one fix slice a watch starts for this merge
request; a second red is reported to the user.

Failed jobs:
{jobs}
A job's log: `glab ci trace <job id> -R {project}`.

Decide which of the three it is, and do only that:
1. The branch caused it: fix it, validate, commit on top (never a rebase, an amend or a force-push:
   other work may branch from this branch; a rebase that is needed goes under FOR ME), push with
   `{push}`, then `/handoff:close`, which registers the watch again from this tree
   (`flowkit-watch add --story {story} --mr {url}`). With a ladder, this grades {by} `rework`.
2. Flaky (the job fails by itself, not by the branch): retry it once
   (`glab ci retry <job id> -R {project}`), change nothing, and close with the watch registered
   again the same way.
3. Red that {target} has already, or that is not this branch's doing: change nothing, register
   nothing, and close with it under FOR ME and `Next: none`.

=== base
{url} went red at {sha} while its base, {base}, was red: {what} ({link}). The base is green again.
Merge it in and push, nothing else:
1. `git fetch origin {base}`, then `git merge origin/{base}` (a merge, never a rebase or a
   force-push); solve what conflicts, validate, commit.
2. Push with `{push}`, then `/handoff:close`, which registers the watch again from this tree
   (`flowkit-watch add --story {story} --mr {url}`). A second red of this merge request is
   reported to the user.

=== tree
The tree is on {branch}, as the slice that pushed left it; `git pull --ff-only` first when origin
has moved.

=== worktree
This tree is a worktree the watch made for you, detached at origin/{branch}, because {why}. Commit
here and push `HEAD` to the branch; never check {branch} out (it is checked out elsewhere). The
worktree goes when the watch ends.

=== checkout
This repository has one checkout, and other slices switch branches in it. Note the branch it is
on when you start (`git branch --show-current`), `git switch {branch}`, and switch back to the one
you found as your last step, after `/handoff:close`.

=== merge
The chain ends with a merge: set auto-merge again after your push (a push cancels it).

=== tail
{needs}The story's last close:

{last}

Chain end: {chain_end}
Stop when: what you decided above is carried out; then close with `/handoff:close`, without
`spawn`: no slice follows you.
