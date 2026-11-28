"""The watch on a GitHub pull request: test_watch.py's sandbox with a fake `gh` in place of `glab`,
which answers hand-written GraphQL per call, so nothing reaches GitHub. Run with
`python3 -m unittest discover plugins/handoff/tests`."""
import json
import os
import shutil
import signal
import subprocess
import sys

import test_watch as base
from test_watch import T0, git

PR = "https://github.com/o/r/pull/12"
CHECKS = f"{PR}/checks"
GH = """#!/bin/sh
d="{dir}"
line="$PWD|"
for a in "$@"; do case "$a" in query=*) a=query;; esac; line="$line $a"; done
echo "$line" >> "$d/calls"
if [ "$1" = pr ]; then code=0; [ -f "$d/pr.exit" ] && read code < "$d/pr.exit"; exit "$code"; fi
n=0; [ -f "$d/count" ] && read n < "$d/count"; n=$((n + 1)); echo "$n" > "$d/count"
for f in "$d/$n" "$d/last"; do
  [ -f "$f.err" ] && {{ cat "$f.err" >&2; exit 1; }}
  [ -f "$f.json" ] && exec cat "$f.json"
done
"""


class GitHub(base.Launches):
    def setUp(self):
        super().setUp()
        self.answers = self.tmp / "gh"
        self.answers.mkdir()
        self.stub("gh", GH.format(dir=self.answers))

    def park(self):
        """The parked poller is one of this version: it says which kinds of arm it asks."""
        super().park()
        (self.dir / "poller.kinds").write_text(json.dumps({"pid": os.getpid(), "kinds": ["command", "github-pr", "github-ref"]}))

    def add(self, *args, **kw):
        return super().add(*(args or ("--pr", PR)), **kw)

    @staticmethod
    def run_(name, end="SUCCESS", job=31, run=7, at="2026-11-17T10:00:00Z"):
        return {"__typename": "CheckRun", "name": name, "conclusion": end, "startedAt": at,
                "detailsUrl": f"https://github.com/o/r/actions/runs/{run}/job/{job}",
                "checkSuite": {"workflowRun": {"workflow": {"name": "tests"}}}}

    @staticmethod
    def commit(state=None, *nodes):
        """GitHub's answer for a commit: its checks rolled up, none when it has no check yet."""
        return {"data": {"repository": {"object": {"statusCheckRollup": state and {"state": state, "contexts": {"nodes": list(nodes)}}}}}}

    def pr(self, state=None, *nodes, sha=None, pull="OPEN", **more):
        """The same with the pull request, whose head is the pushed sha unless told otherwise."""
        found = self.commit(state, *nodes)
        found["data"]["repository"]["pullRequest"] = {
            "state": pull, "headRefOid": sha or self.pushed, "baseRefName": "main", "mergeable": "MERGEABLE",
            "mergeStateStatus": "CLEAN", "reviewDecision": None, "autoMergeRequest": None, "mergeCommit": None, **more}
        return found

    def asked(self, sha, number=True):
        return (f"{self.repo}| api graphql --hostname github.com -f query -f owner=o -f name=r -f oid={sha}"
                + (" -F number=12" if number else ""))

    def last(self):
        return self.story().split("## ")[-1]

    def test_add_reads_the_host_from_the_url_and_asks_nothing(self):
        out = self.watch("add", "--story", "st", "--mr", PR + "/files", at=T0)
        self.assertEqual((out.stdout, out.stderr), (f"watching repo!12 · sha {self.pushed[:7]} · until green\n", ""))
        arm = self.arm()
        want = {"name": "repo!12", "kind": "github-pr", "project": "o/r", "iid": 12, "url": PR, "host": "github.com",
                "gh": str(self.tmp / "bin" / "gh"), "glab": "", "sha": self.pushed, "branch": "feature", "state": "pending"}
        self.assertEqual({k: arm[k] for k in want}, want)
        self.assertEqual(self.add(at=T0 + 60), "repo!12", "--pr is the same flag: the same arm, armed again")
        self.assertEqual((self.calls(), self.ops()), ([], ["add", "arm"]))
        (self.tmp / "bin" / "gh").unlink()
        (self.tmp / "bin" / "git").symlink_to(shutil.which("git"))  # a PATH of the stubs and git alone: gh may live beside git
        out = self.watch("add", "--story", "st", "--pr", "https://github.com/o/r/pull/13", env={"PATH": str(self.tmp / "bin")})
        self.assertEqual((out.returncode, out.stderr), (1, "gh is not on the PATH: a GitHub watch asks through it\n"))

    def test_pending_then_green_is_a_section(self):
        self.add("--pr", PR, "--left", "- S2 next")
        self.poll(T0 + 60, self.pr("PENDING", self.run_("test", None)))
        self.assertEqual((self.arm()["answer"], self.story(), self.calls()), ("checks pending", "", [self.asked(self.pushed)]))
        self.poll(T0 + 120, self.pr("SUCCESS", self.run_("test")))
        self.assertEqual(self.story(), "\n".join([
            "# st", "", f"## repo!12 · {self.stamp(T0 + 120)}",
            f"Watch: checks green {self.clock(T0 + 120)} · sha {self.pushed[:7]} · 2 min · {PR}", "Next: none",
            "DONE - checks green", "FOR ME - nothing new", "OPEN - nothing", "LEFT - S2 next", ""]))
        self.assertEqual((self.ops(), self.arm()), (["add", "green", "done"], None))
        self.assertFalse((self.tmp / "glab" / "calls").exists(), "a pull request asks glab nothing")

    def test_checks_that_never_start_are_reported_after_15_minutes(self):
        self.add()
        self.poll(T0 + 15 * 60, self.pr())
        self.assertEqual((self.arm()["answer"], self.story()), ("no checks yet", ""))
        self.poll(T0 + 15 * 60 + 1, self.pr())
        self.assertIn(f"FOR ME - 1. no checks for {self.pushed[:7]} after 15 min: {PR}\n", self.story())

    def test_the_ends_that_need_the_user(self):
        for n, (answer, what, where) in enumerate([
                (self.pr(pull="CLOSED"), "PR closed", PR),
                (self.pr("FAILURE", self.run_("test", "CANCELLED")), "checks cancelled", CHECKS),
                (self.pr("FAILURE", self.run_("deploy", "ACTION_REQUIRED"), self.run_("test")), "checks action required", CHECKS)], 1):
            name = self.add()
            self.poll(T0 + 60, answer)
            with self.subTest(what):
                self.assertIn(f"\nWatch: {what} ", self.last())
                self.assertIn(f"\nDONE - nothing new\nFOR ME - 1. {what}: {where}\nOPEN - {what}\n", self.last())
                self.assertEqual((self.ops(name), self.spawned()), (["add", "report", "done"], []))
        self.add()
        self.poll(T0 + 60, self.pr("SUCCESS", pull="MERGED"))
        self.assertIn("\nDONE - PR merged\nFOR ME - nothing new\n", self.last())
        self.add()
        for n in range(1, 16):
            self.poll(T0 + n * 60, last="gh: To get started with GitHub CLI, please run:  gh auth login")
        self.assertIn(f"FOR ME - 1. watch broken: gh: To get started with GitHub CLI, please run:  gh auth login: {PR}\n", self.last())

    def test_a_moved_head_retargets(self):
        self.add()
        self.poll(T0 + 60, self.pr("FAILURE", self.run_("test", "FAILURE"), sha="b" * 40))
        self.assertEqual((self.arm()["sha"], self.arm()["retargets"]), ("b" * 40, 1))
        self.assertEqual(self.story(), f"# st\n> watch repo!12 · retarget bbbbbbb · {self.stamp(T0 + 60)}\n")
        self.poll(T0 + 120, self.pr("SUCCESS", sha="b" * 40))
        self.assertEqual(self.calls()[-1], self.asked("b" * 40), "the checks asked for are the new head's")
        self.assertIn("Watch: checks green ", self.story())

    def test_ready_takes_the_pr_out_of_draft(self):
        self.add("--pr", PR, "--ready")
        self.poll(T0 + 60, self.pr("SUCCESS"))
        self.assertEqual(self.calls()[-1], f"{self.repo}| pr ready {PR}")
        self.assertIn("DONE - checks green; PR ready\nFOR ME - nothing new\n", self.story())
        self.add("--pr", PR, "--ready")
        self.says(self.pr("SUCCESS"))
        (self.answers / "pr.exit").write_text("1")
        self.poll(T0 + 60)
        self.assertIn(f"DONE - checks green; PR still Draft\nFOR ME - 1. take the PR out of Draft, the watch could not "
                      f"(gh exited 1): {PR}\n", self.last())

    def test_the_first_red_starts_one_fix_slice_and_the_second_is_reported(self):
        self.session()
        self.add()
        failed = self.pr("FAILURE", self.run_("test (ubuntu-latest)", "FAILURE", job=33, at="2026-11-17T10:05:00Z"),
                         self.run_("test (ubuntu-latest)", "SUCCESS", job=34, run=8),  # the same job for another event
                         self.run_("test (macos-latest)", "FAILURE", job=31),  # an older try of the job below
                         self.run_("test (macos-latest)", "SUCCESS", job=32, at="2026-11-17T10:09:00Z"),
                         {"__typename": "StatusContext", "context": "preview", "state": "ERROR", "targetUrl": "https://ci.example.com/1",
                          "createdAt": "2026-11-17T10:01:00Z"})
        self.poll(T0 + 60, failed)
        self.assertEqual(self.section(), [
            self.stamp(T0 + 60), f"Watch: checks red {self.clock(T0 + 60)} · sha {self.pushed[:7]} · 1 min · {PR}",
            "Next: started st-fix-1", f"DONE - fix slice st-fix-1 started (auto) in {self.repo}",
            "FOR ME - nothing new", f"OPEN - checks red: {CHECKS}", "LEFT - nothing left"])
        self.assertEqual((self.arm()["state"], self.ops(), len(self.calls())), ("fixing", ["add", "red", "fix"], 1),
                         "the failed checks came with the answer: nothing more is asked")
        brief = (self.claude / "spawned" / "st-fix-1.md").read_text()
        for line in (f"{PR} is red at {self.pushed[:12]}: checks red ({CHECKS}). You are the one fix slice a watch starts for this "
                     "pull request;",
                     "Failed jobs:\n- preview: https://ci.example.com/1\n"
                     "- test (ubuntu-latest) (tests, job 33): https://github.com/o/r/actions/runs/7/job/33\nA job's log: "
                     "`gh run view --job <job id> --log-failed -R o/r`.",
                     "(`gh run rerun --job <job id> -R o/r`)", f"(`flowkit-watch add --story st --mr {PR}`)"):
            self.assertIn(line, brief)
        self.assertNotIn("glab", brief)
        self.assertNotIn("{", brief, "every word is filled in")
        git(self.repo, "commit", "-q", "--allow-empty", "-m", "fix")
        git(self.repo, "push", "-q", "origin", "HEAD:refs/heads/feature")
        sha = git(self.repo, "rev-parse", "HEAD")
        self.assertEqual(self.add("--mr", PR, at=T0 + 600), "repo!12#2", "the fix slice's close arms the next name")
        self.poll(T0 + 660, self.pr("FAILURE", self.run_("test (ubuntu-latest)", "FAILURE"), sha=sha), name="repo!12#2")
        self.assertEqual(self.last().splitlines()[1:6], [
            f"Watch: red again after one fix: test (ubuntu-latest) {self.clock(T0 + 660)} · sha {sha[:7]} · 1 min · {PR}", "Next: none",
            "DONE - nothing new", f"FOR ME - 1. red again after one fix: test (ubuntu-latest): {CHECKS}",
            "OPEN - red again after one fix: test (ubuntu-latest)"])
        self.assertEqual((len(self.spawned()), self.arm("repo!12#2")), (1, None))

    def test_until_merged_waits_for_the_merge_then_the_targets_checks(self):
        self.add("--pr", PR, "--until", "merged", "--left", "nothing left")
        for n in (1, 2, 3):
            self.poll(T0 + n * 60, self.pr("SUCCESS"))
        self.assertEqual((self.arm()["phase"], self.ops()), ("merge", ["add", "green"]))
        self.assertEqual(self.story(), f"# st\n> watch repo!12 · waiting for the merge: auto-merge is off · {self.stamp(T0 + 120)}\n",
                         "said once, and the watch goes on: GitHub does not say that an auto-merge was cancelled")
        self.poll(T0 + 240, self.pr("SUCCESS", autoMergeRequest={"enabledAt": "2026-11-17T10:00:00Z"}, reviewDecision="REVIEW_REQUIRED",
                                    mergeStateStatus="BLOCKED"))
        self.assertIn(f"\n> watch repo!12 · waiting for approval · {self.stamp(T0 + 240)}\n", self.story())
        git(self.origin, "update-ref", "refs/heads/main", self.pushed)
        self.poll(T0 + 300, self.pr("SUCCESS", pull="MERGED", mergeCommit={"oid": self.pushed}))
        arm = self.arm()
        self.assertEqual((arm["phase"], arm["merged_sha"], arm["target"]), ("target", self.pushed, "main"))
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "main", "switched to the default branch")
        self.poll(T0 + 360, self.commit())
        self.assertEqual((self.arm()["answer"], self.calls()[-1]), ("no checks yet", self.asked(self.pushed, number=False)))
        self.poll(T0 + 420, self.commit("SUCCESS"))
        self.assertEqual(self.story().split("\n\n")[1], "\n".join([
            f"## repo!12 · {self.stamp(T0 + 420)}",
            f"Watch: merged; main checks green {self.clock(T0 + 420)} · sha {self.pushed[:7]} · 7 min · {PR}",
            "Next: none", "DONE - merged; main checks green; back on main, feature kept (it has commits the merge did not take)",
            "FOR ME - nothing new", "OPEN - nothing", "LEFT - nothing left", ""]))
        self.assertEqual(self.ops(), ["add", "green", "merged", "done"])

    def test_a_conflict_ends_the_wait_at_the_second_poll_and_a_red_target_is_reported(self):
        name = self.add("--pr", PR, "--until", "merged")
        self.poll(T0 + 60, self.pr("SUCCESS"))
        self.poll(T0 + 120, self.pr("SUCCESS", mergeable="CONFLICTING", mergeStateStatus="DIRTY"))
        self.assertEqual(self.arm(name)["answer"], "merge blocked: conflict")
        self.poll(T0 + 180, self.pr("SUCCESS", mergeable="CONFLICTING", mergeStateStatus="DIRTY"))
        self.assertIn(f"FOR ME - 1. merge blocked: conflict: {PR}\n", self.last())
        name = self.add("--pr", PR, "--until", "merged")
        self.poll(T0 + 60, self.pr("SUCCESS", pull="MERGED", mergeCommit={"oid": "c" * 40}), name=name)
        self.poll(T0 + 120, self.commit("FAILURE", self.run_("test", "FAILURE")), name=name)
        self.assertIn(f"FOR ME - 1. merged; main checks red: https://github.com/o/r/commit/{'c' * 40}/checks\n", self.last())
        self.assertEqual((self.ops(name), self.spawned()), (["add", "merged", "report", "done"], []))

    def test_a_ref_of_a_github_repository_asks_for_its_checks(self):
        git(self.repo, "remote", "set-url", "origin", "git@github.com:o/r.git")
        main = git(self.repo, "rev-parse", "origin/main")
        self.assertEqual(self.add("--ref", "main"), "repo@main")
        arm = self.arm("repo@main")
        self.assertEqual((arm["kind"], arm["project"], arm["gh"]), ("github-ref", "o/r", str(self.tmp / "bin" / "gh")))
        self.poll(T0 + 60, self.commit("PENDING"))
        self.assertEqual((self.arm("repo@main")["answer"], self.calls()), ("main checks pending", [self.asked(main, number=False)]))
        self.poll(T0 + 120, self.commit("SUCCESS"))
        self.assertIn(f"Watch: main checks green {self.clock(T0 + 120)} · sha {main[:7]} · 2 min\n", self.story())

    def test_a_poller_of_an_older_version_makes_way_for_one_that_asks_github(self):
        old = self.tmp / "old" / "watch.py"
        old.parent.mkdir()
        old.write_text("import time\ntime.sleep(60)\n")
        # nobody's child, as a poller is: one that ended would stay a zombie of this test, and count as alive
        pid = int(subprocess.run(["sh", "-c", f'"{sys.executable}" "{old}" run > /dev/null 2>&1 & echo $!'],
                                 capture_output=True, text=True, check=True).stdout)
        self.addCleanup(lambda: alive() and os.kill(pid, signal.SIGKILL))

        def alive():
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False

        (self.dir / "poller.pid").write_text(str(pid))
        super().add("--mr", base.MR)
        self.assertTrue(alive(), "it asks GitLab as it always did: left alone")
        self.says(last=self.pr("PENDING"))
        name = self.add(env={"FLOWKIT_WATCH_POLL": "0.2"})
        self.assertFalse(alive(), "it would have asked glab for a pull request")

        def taken():
            try:
                said = json.loads((self.dir / "poller.kinds").read_text())
                return said["pid"] == int((self.dir / "poller.pid").read_text()) != pid and "github-pr" in said["kinds"]
            except (OSError, ValueError):
                return False

        self.until(taken, "the poller that add started in its place")
        self.until(lambda: self.arm(name)["answer"] == "checks pending", "its first ask of the pull request")
