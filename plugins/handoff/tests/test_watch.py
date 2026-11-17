"""Run with `python3 -m unittest discover plugins/handoff/tests`. Each test gets its own config dir, a
sandbox repository whose HEAD is one doc commit ahead of `@{u}` (a close commits after its push), a
fake `glab` that answers hand-written JSON per call, stub `claude`, `tmux` and `caffeinate`, and a
clock it sets, so nothing reaches GitLab, a real session or the user's files."""
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parent.parent
WATCH = PLUGIN / "scripts" / "watch.py"
MR = "https://gitlab.com/g/p/-/merge_requests/12"
T0 = 1_800_000_000.0
GIT = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
GLAB = """#!/bin/sh
d="{dir}"
echo "$PWD|$*" >> "$d/calls"
if [ "$1" = mr ]; then code=0; [ -f "$d/mr.exit" ] && read code < "$d/mr.exit"; exit "$code"; fi
[ -f "$d/sleep" ] && {{ read s < "$d/sleep"; sleep "$s"; }}
n=0; [ -f "$d/count" ] && read n < "$d/count"; n=$((n + 1)); echo "$n" > "$d/count"
for f in "$d/$n" "$d/last"; do
  [ -f "$f.err" ] && {{ cat "$f.err" >&2; exit 1; }}
  [ -f "$f.json" ] && exec cat "$f.json"
done
"""


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True,
                          env={**os.environ, **GIT}).stdout.strip()


class Watch(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = Path(tempfile.mkdtemp())
        origin, repo = cls.template / "origin.git", cls.template / "repo"
        git(cls.template, "init", "-q", "--bare", "-b", "main", str(origin))
        git(cls.template, "init", "-q", "-b", "main", str(repo))
        git(repo, "commit", "-q", "--allow-empty", "-m", "one")
        git(repo, "remote", "add", "origin", str(origin))
        git(repo, "push", "-q", "-u", "origin", "main")
        git(repo, "checkout", "-q", "-b", "feature")
        git(repo, "commit", "-q", "--allow-empty", "-m", "work")
        git(repo, "push", "-q", "-u", "origin", "feature")
        git(repo, "commit", "-q", "--allow-empty", "-m", "docs: the close's pick-up block")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.template, ignore_errors=True)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        shutil.copytree(self.template, self.tmp, dirs_exist_ok=True)
        self.repo, self.origin = self.tmp / "repo", self.tmp / "origin.git"
        git(self.repo, "remote", "set-url", "origin", str(self.origin))
        self.pushed = git(self.repo, "rev-parse", "@{u}")
        self.claude = self.tmp / "claude"
        self.dir = self.claude / "flowkit" / "watches"
        self.answers = self.tmp / "glab"
        for d in (self.tmp / "bin", self.answers, self.dir):
            d.mkdir(parents=True)
        self.stub("glab", GLAB.format(dir=self.answers))
        self.stub("claude", f"#!/bin/sh\n[ \"$1\" = agents ] && exec cat {self.tmp / 'agents.json'}\n")
        self.stub("tmux", "#!/bin/sh\nexit 0\n")
        self.stub("caffeinate", f"#!/bin/sh\necho \"$@\" >> {self.tmp / 'caffeinate.calls'}\nexec sleep 60\n")
        (self.tmp / "agents.json").write_text("[]")
        self.env = {**{k: v for k, v in os.environ.items() if not k.startswith(("FLOWKIT_", "TMUX"))}, **GIT,
                    "CLAUDE_CONFIG_DIR": str(self.claude), "XDG_CONFIG_HOME": str(self.tmp / "xdg"), "HOME": str(self.tmp),
                    "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}", "CLAUDE_PID": "999999"}
        self.park()

    def tearDown(self):
        try:
            pid = int((self.dir / "poller.pid").read_text())
            if pid != os.getpid():  # a poller a test started; the parked pid is this process
                os.kill(pid, signal.SIGTERM)
        except (OSError, ValueError):
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def stub(self, name, text):
        path = self.tmp / "bin" / name
        path.write_text(text)
        path.chmod(0o755)

    def park(self):
        """A fresh poller.pid of a live process: `add` and `ensure` start no poller of their own."""
        (self.dir / "poller.pid").write_text(str(os.getpid()))

    def watch(self, *args, at=None, env=None, cwd=None):
        return subprocess.run([sys.executable, str(WATCH), *args], cwd=cwd or self.repo, capture_output=True, text=True,
                              env={**self.env, **({"FLOWKIT_WATCH_NOW": str(at)} if at else {}), **(env or {})})

    def add(self, *args, at=T0, **kw):
        out = self.watch("add", "--story", "st", *(args or ("--mr", MR)), at=at, **kw)
        self.assertEqual((out.returncode, out.stderr), (0, ""))
        return out.stdout.split()[1]

    def says(self, *answers, last=None):
        """The fake glab's next answers, one per call (a str is its stderr with exit 1), then `last` for ever."""
        for old in self.answers.glob("*"):
            if old.name != "calls":
                old.unlink()
        for n, a in enumerate([*answers, last], 1):
            if a is not None:
                name = "last" if n > len(answers) else str(n)
                (self.answers / f"{name}.{'err' if isinstance(a, str) else 'json'}").write_text(a if isinstance(a, str) else json.dumps(a))

    def mr(self, status=None, sha=None, pipeline_sha=None, state="opened", **more):
        sha = sha or self.pushed
        head = status and {"id": 7, "sha": pipeline_sha or sha, "status": status, "web_url": "https://gitlab.com/g/p/-/pipelines/7"}
        return {"state": state, "sha": sha, "target_branch": "main", "head_pipeline": head, **more}

    def poll(self, at, *answers, name=None, **kw):
        if answers or kw:
            self.says(*answers, **kw)
        out = self.watch("poll", *([name] if name else []), at=at)
        self.assertEqual((out.returncode, out.stderr), (0, ""))

    def arm(self, name="repo!12"):
        path = self.dir / f"{name}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def story(self):
        path = self.claude / "stories" / "st.md"
        return path.read_text() if path.exists() else ""

    def ops(self, name=None):
        path = self.claude / "capacity" / "log.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [e["op"] for e in events if e["event"] == "watch" and name in (None, e["name"])]

    def calls(self):
        path = self.answers / "calls"
        return path.read_text().splitlines() if path.exists() else []

    def until(self, test, what, seconds=10):
        deadline = time.time() + seconds
        while not test():
            self.assertLess(time.time(), deadline, f"waited {seconds} s for {what}")
            time.sleep(0.05)

    @staticmethod
    def stamp(t, fmt="%Y-%m-%d %H:%M"):
        return time.strftime(fmt, time.localtime(t))


class Register(Watch):
    def test_add_arms_the_pushed_sha_not_head(self):
        out = self.watch("add", "--story", "St", "--mr", MR + "/diffs", "--left", "- S2 next", at=T0)
        self.assertEqual((out.stdout, out.stderr), (f"watching repo!12 · sha {self.pushed[:7]} · until green\n", ""))
        arm = self.arm()
        self.assertNotEqual(arm["sha"], git(self.repo, "rev-parse", "HEAD"), "HEAD is the close's doc commit")
        want = {"name": "repo!12", "story": "st", "kind": "gitlab-mr", "project": "g/p", "iid": 12, "url": MR,
                "sha": self.pushed, "tree": str(self.repo), "branch": "feature", "glab": str(self.tmp / "bin" / "glab"),
                "until": "green", "ready": False, "state": "pending", "armed": T0, "rev": 1}
        self.assertEqual({k: arm[k] for k in want}, want)
        self.assertEqual(arm["by"]["left"], "- S2 next")
        self.assertEqual(self.ops(), ["add"])
        self.assertEqual(self.calls(), [], "registering asks GitLab nothing: a close never waits for the network")

    def test_a_name_is_given_once_and_a_pending_arm_is_armed_again_in_place(self):
        self.assertEqual(self.add(), "repo!12")
        git(self.repo, "push", "-q")
        self.assertEqual(self.add(at=T0 + 60), "repo!12", "still pending: the same arm, at the new push")
        arm = self.arm()
        self.assertEqual((arm["sha"], arm["armed"], arm["rev"]), (git(self.repo, "rev-parse", "HEAD"), T0 + 60, 2))
        self.assertEqual(self.watch("drop", "repo!12", at=T0 + 120).stdout, "dropped repo!12\n")
        self.assertEqual(self.add(at=T0 + 180), "repo!12#2", "repo!12 has a section: it never names another arm")
        self.assertEqual(self.ops(), ["add", "arm", "done", "add"])
        self.assertEqual(self.add("--check", "exit 2", "--name", "repo!12"), "repo!12#3")

    def test_what_add_refuses(self):
        def refused(*args, **kw):
            out = self.watch("add", "--story", "st", *args, **kw)
            self.assertEqual((out.returncode, out.stdout), (1, ""))
            return out.stderr
        self.assertIn("merge request's URL", refused("--mr", "https://gitlab.com/g/p/-/issues/12"))
        self.assertIn("--ready ends a watch at green", refused("--mr", MR, "--ready", "--until", "merged"))
        self.assertIn("--check needs --name", refused("--check", "true"))
        dirs = [d for d in os.environ["PATH"].split(os.pathsep) if not os.access(os.path.join(d, "glab"), os.X_OK)]
        (self.tmp / "bin" / "glab").unlink()
        self.assertIn("glab is not on the PATH", refused("--mr", MR, env={"PATH": os.pathsep.join([str(self.tmp / "bin"), *dirs])}))
        git(self.repo, "checkout", "-q", "-b", "unpushed")
        self.assertIn("has no upstream", refused("--mr", MR))
        self.assertEqual(list(self.dir.glob("*.json")), [])

    def test_list_and_drop(self):
        self.assertEqual(self.watch("list").stdout, "no watch is open\n")
        self.add("--mr", MR, "--left", "nothing left")
        self.assertEqual(self.watch("list").stdout,
                         f"repo!12 · st · pending · sha {self.pushed[:7]} · armed {self.stamp(T0, '%H:%M')}\n")
        self.assertEqual(json.loads(self.watch("list", "--json").stdout)[0]["name"], "repo!12")
        self.assertIn("no watch named 'nope'", self.watch("drop", "nope").stderr)
        self.watch("drop", "repo!12", at=T0 + 300)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["poller.pid"], "the arm's file and lock went")
        self.assertEqual(self.story(), "\n".join([
            "# st", "", f"## repo!12 · {self.stamp(T0 + 300)}",
            f"Watch: dropped {self.stamp(T0 + 300, '%H:%M')} · sha {self.pushed[:7]} · 5 min · {MR}", "Next: none",
            "DONE - nothing new", "FOR ME - nothing new", f"OPEN - no longer watched: {MR}", "LEFT - nothing left", ""]))


class Poller(Watch):
    def probe(self):
        """A command arm that never answers, and writes what its poller hands a child."""
        return self.add("--check", f"env > {self.tmp / 'env.out'}; exit 2", "--name", "probe",
                        env={"FLOWKIT_WATCH_POLL": "0.2", "TMUX_PANE": "%1", "CLAUDE_CODE_ENTRYPOINT": "cli"})

    def pid(self):
        try:
            return int((self.dir / "poller.pid").read_text())
        except (OSError, ValueError):
            return None

    def test_one_poller_that_beats_keeps_awake_and_scrubs_its_children(self):
        (self.dir / "poller.pid").unlink()
        self.probe()
        self.until(lambda: (self.tmp / "env.out").exists() and self.pid(), "the poller add started")
        pid, beat = self.pid(), (self.dir / "poller.pid").stat().st_mtime
        second = self.watch("run", env={"FLOWKIT_WATCH_POLL": "0.2"})
        self.assertEqual((second.stdout, self.pid()), (f"a poller runs already (pid {pid})\n", pid))
        self.until(lambda: (self.dir / "poller.pid").stat().st_mtime > beat, "the next heartbeat")
        self.until((self.tmp / "caffeinate.calls").exists, "the keep-awake")
        self.assertEqual((self.tmp / "caffeinate.calls").read_text(), f"-i -w {pid}\n")
        keys = {line.partition("=")[0] for line in (self.tmp / "env.out").read_text().splitlines()}
        self.assertEqual(keys & {"CLAUDE_PID", "TMUX_PANE", "CLAUDE_CODE_ENTRYPOINT"}, set(),
                         "a child that kept them would be taken for the session that registered the watch")
        self.assertIn("CLAUDE_CONFIG_DIR", keys)
        self.watch("drop", "probe")
        self.until(lambda: self.pid() is None, "the poller to go with its last watch")
        self.assertRaises(OSError, os.kill, pid, 0)

    def test_a_dead_pollers_pidfile_is_replaced_by_ensure(self):
        self.watch("ensure")
        self.assertEqual(self.pid(), os.getpid(), "no watch is open: nothing to start")
        self.probe()
        self.watch("ensure")
        self.assertEqual(self.pid(), os.getpid(), "a live poller with a fresh heartbeat holds it")
        dead = subprocess.Popen(["true"])
        dead.wait()
        (self.dir / "poller.pid").write_text(str(dead.pid))
        self.watch("ensure", env={"FLOWKIT_WATCH_POLL": "0.2"})
        self.until(lambda: self.pid() not in (None, dead.pid), "a new poller")
        os.utime(self.dir / "poller.pid", (0, 0))
        self.assertEqual(self.watch("run").stdout, f"a poller runs already (pid {self.pid()})\n",
                         "a stale heartbeat of a process that still is the poller: a Mac that slept")

    def test_of_two_polls_one_wins(self):
        self.add()
        self.says(last=self.mr("success"))
        (self.answers / "sleep").write_text("0.5")
        env = {**self.env, "FLOWKIT_WATCH_NOW": str(T0 + 60)}
        both = [subprocess.Popen([sys.executable, str(WATCH), "poll"], cwd=self.repo, env=env) for _ in range(2)]
        self.assertEqual([p.wait() for p in both], [0, 0])
        self.assertEqual(len([c for c in self.calls() if "merge_requests/12" in c]), 2, "both asked")
        self.assertEqual(self.story().count("## repo!12 · "), 1)
        self.assertEqual(self.ops(), ["add", "green", "done"])
        self.assertIsNone(self.arm())


class Answers(Watch):
    def test_pending_then_green_is_a_section_through_record(self):
        self.add("--mr", MR, "--left", "- S2 next")
        self.poll(T0 + 60, self.mr("running"))
        self.assertEqual((self.arm()["answer"], self.story(), self.ops()), ("running", "", ["add"]))
        self.assertEqual(self.calls(), [f"{self.repo}|api projects/g%2Fp/merge_requests/12"], "asked in the watch's tree")
        self.poll(T0 + 120, self.mr("success"))
        self.assertEqual(self.story(), "\n".join([
            "# st", "", f"## repo!12 · {self.stamp(T0 + 120)}",
            f"Watch: pipeline green {self.stamp(T0 + 120, '%H:%M')} · sha {self.pushed[:7]} · 2 min · {MR}", "Next: none",
            "DONE - pipeline green", "FOR ME - nothing new", "OPEN - nothing", "LEFT - S2 next", ""]))
        self.assertEqual(self.ops(), ["add", "green", "done"])
        self.assertEqual([p.name for p in self.dir.glob("repo*")], [], "the arm's file and lock go with its section")

    def test_the_ends_that_need_the_user(self):
        link = "https://gitlab.com/g/p/-/pipelines/7"
        for n, (answer, what, where, op) in enumerate([
                (self.mr("failed"), "pipeline red", link, "red"), (self.mr("canceled"), "pipeline canceled", link, "report"),
                (self.mr("skipped"), "pipeline skipped", link, "report"), (self.mr("manual"), "pipeline manual", link, "report"),
                (self.mr(state="closed"), "MR closed", MR, "report")], 1):
            name = self.add()
            self.poll(T0 + 60, answer)
            with self.subTest(what):
                self.assertEqual(name, "repo!12" if n == 1 else f"repo!12#{n}")
                self.assertIn(f"\nWatch: {what} ", self.story().split("## ")[-1])
                self.assertIn(f"\nDONE - nothing new\nFOR ME - 1. {what}: {where}\nOPEN - {what}\n", self.story().split("## ")[-1])
                self.assertEqual(self.ops(name), ["add", op, "done"])
        self.add()
        self.poll(T0 + 60, self.mr("success", state="merged"))
        self.assertIn("\nDONE - MR merged\nFOR ME - nothing new\n", self.story().split("## ")[-1])

    def test_a_head_with_no_pipeline_is_reported_after_15_minutes(self):
        self.add()
        self.poll(T0 + 15 * 60, self.mr())
        self.assertEqual((self.arm()["answer"], self.story()), ("no pipeline yet", ""))
        self.poll(T0 + 15 * 60 + 1, self.mr())
        self.assertIn(f"FOR ME - 1. no pipeline for {self.pushed[:7]} after 15 min: {MR}\n", self.story())

    def test_a_moved_head_retargets_and_its_old_red_is_never_reported(self):
        self.add()
        moved = "b" * 40
        self.poll(T0 + 60, self.mr("failed", sha=moved, pipeline_sha=self.pushed))
        arm = self.arm()
        self.assertEqual((arm["sha"], arm["targeted"], arm["armed"], arm["retargets"]), (moved, T0 + 60, T0, 1))
        self.assertEqual(self.story(), f"# st\n> watch repo!12 · retarget bbbbbbb · {self.stamp(T0 + 60)}\n",
                         "an interim line, never a section: the promise stays open")
        self.assertEqual(self.ops(), ["add", "retarget"])
        self.poll(T0 + 16 * 60, self.mr("running", sha=moved))
        self.assertEqual(self.arm()["answer"], "running", "the new head has its own 15 minutes")
        self.poll(T0 + 17 * 60, self.mr("success", sha=moved))
        self.assertIn("Watch: pipeline green ", self.story())
        self.assertIn(" · sha bbbbbbb · 17 min · ", self.story())

    def test_a_retarget_racing_an_add_loses(self):
        self.add()
        git(self.repo, "push", "-q")
        with mock.patch.dict(os.environ, {**self.env, "FLOWKIT_WATCH_NOW": str(T0 + 60)}):
            spec = importlib.util.spec_from_file_location("flowkit_watch", WATCH)
            watch = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(watch)

            def ask(arm, t):  # the answer is on its way while the fix's close arms the same MR again
                self.add(at=T0 + 61)
                return watch.answer("retarget", "retarget bbbbbbb", sha="b" * 40, target="main")
            with mock.patch.object(watch, "ask", ask):
                watch.poll("repo!12")
        arm = self.arm()
        self.assertEqual((arm["sha"], arm["armed"], arm["retargets"]), (git(self.repo, "rev-parse", "HEAD"), T0 + 61, 0))
        self.assertEqual((self.ops(), self.story()), (["add", "arm"], ""))

    def test_a_merged_results_pipeline_counts_at_the_second_poll(self):
        """Its sha is the merge result's, so right after a push it cannot be told from the last head's."""
        self.add()
        merged_result = self.mr("failed", pipeline_sha="c" * 40)
        self.poll(T0 + 60, merged_result)
        self.assertEqual((self.arm()["answer"], self.story()), ("failed, asked once more", ""))
        self.poll(T0 + 120, self.mr("running", pipeline_sha="c" * 40))
        self.poll(T0 + 180, self.mr("success", pipeline_sha="c" * 40))
        self.assertEqual(self.story(), "", "another status than the one seen: asked once more again")
        self.poll(T0 + 240, self.mr("success", pipeline_sha="c" * 40))
        self.assertIn("DONE - pipeline green\n", self.story())

    def test_fifteen_errors_in_a_row_are_a_broken_watch(self):
        self.add()
        for n in range(1, 15):
            self.poll(T0 + n * 60, last="glab: 401 Unauthorized")
        self.assertEqual((self.arm()["errors"], self.arm()["answer"], self.ops()), (14, "error: glab: 401 Unauthorized", ["add", "error"]))
        self.poll(T0 + 15 * 60, self.mr("running"))
        self.assertEqual(self.arm()["errors"], 0, "an answer ends the row")
        for n in range(16, 31):
            self.poll(T0 + n * 60, last="glab: 401 Unauthorized")
        self.assertIn(f"FOR ME - 1. watch broken: glab: 401 Unauthorized: {MR}\n", self.story())
        self.assertEqual(self.ops(), ["add", "error", "error", "report", "done"])

    def test_the_deadline_is_six_hours_from_the_arm(self):
        self.add()
        self.poll(T0 + 6 * 3600, self.mr("running"))
        self.assertEqual(self.story(), "")
        self.poll(T0 + 6 * 3600 + 1, self.mr("running"))
        self.assertIn(f"FOR ME - 1. no result in 6 h (last answer: running): {MR}\n", self.story())
        self.assertEqual(self.ops(), ["add", "deadline", "done"])

    def test_a_ref_arm_asks_for_the_refs_pipeline_at_its_sha(self):
        main = git(self.repo, "rev-parse", "origin/main")
        self.assertEqual(self.add("--ref", "main", "--project", "g/p"), "repo@main")
        self.poll(T0 + 60, [])
        self.assertEqual(self.calls(), [f"{self.repo}|api projects/g%2Fp/pipelines?ref=main&sha={main}"])
        self.poll(T0 + 120, [{"status": "success", "web_url": "u"}, {"status": "failed"}])
        self.assertIn(f"Watch: main pipeline green {self.stamp(T0 + 120, '%H:%M')} · sha {main[:7]} · 2 min\n", self.story())

    def test_a_command_arm_answers_by_its_exit(self):
        code = self.tmp / "code"
        name = self.add("--check", f'echo "$FLOWKIT_WATCH_NAME in $PWD"; exit $(cat {code})', "--name", "build")
        code.write_text("2")
        self.poll(T0 + 60)
        self.assertEqual(self.arm(name)["answer"], f"build in {self.repo}")
        code.write_text("3")
        self.poll(T0 + 120)
        self.assertEqual(self.arm(name)["errors"], 1, "any other exit is an error")
        code.write_text("1")
        self.poll(T0 + 180)
        self.assertIn(f"FOR ME - 1. check red: build in {self.repo}: build\n", self.story())
        self.assertEqual(self.calls(), [], "no glab for a command")

    def test_a_repos_check_answers_for_its_merge_requests(self):
        conf = self.tmp / "xdg" / "flowkit" / "capacity.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text(f"[{self.repo}]\nslots = 1\ncheck = echo device tests passed on $FLOWKIT_WATCH_SHA\n")
        self.add()
        self.poll(T0 + 60, self.mr("failed", sha="b" * 40))
        self.assertEqual(self.arm()["sha"], "b" * 40, "the MR still says where the head is")
        self.poll(T0 + 120, self.mr("failed", sha="b" * 40))
        self.assertIn(f"DONE - check green: device tests passed on {'b' * 40}\n", self.story())


class Green(Watch):
    def test_ready_takes_the_mr_out_of_draft_before_the_section(self):
        self.add("--mr", MR, "--ready", "--left", "nothing left")
        self.poll(T0 + 60, self.mr("success"))
        self.assertEqual(self.calls()[-1], f"{self.repo}|mr update 12 --ready")
        self.assertIn("DONE - pipeline green; MR ready\nFOR ME - nothing new\n", self.story())
        self.add("--mr", MR, "--ready")
        self.says(self.mr("success"))
        (self.answers / "mr.exit").write_text("1")
        self.poll(T0 + 60)
        self.assertIn(f"DONE - pipeline green; MR still Draft\nFOR ME - 1. take the MR out of Draft, the watch could not "
                      f"(glab exited 1): {MR}\n", self.story())

    def test_left_comes_from_the_close_that_registered_the_watch(self):
        sid = "11111111-2222-3333-4444-555555555555"
        self.add("--mr", MR, "--session", sid)
        (self.claude / "stories").mkdir()
        (self.claude / "stories" / "st.md").write_text(
            f"# st\n\n## s5 · 2027-01-15 08:00\nSession: {sid} · Repo: /Users/me/repo\nNext: watching repo!12\nDONE\n- pushed\n"
            "FOR ME\n- nothing\nOPEN\n- nothing\nLEFT\n- the review\n- the release\n> launched other · 2027-01-15 08:01\n")
        self.poll(T0 + 60, self.mr("success"))
        self.assertTrue(self.story().endswith("OPEN - nothing\nLEFT\n- the review\n- the release\n"), self.story())

    def test_a_close_its_poller_did_not_finish_is_finished_once(self):
        self.add("--mr", MR, "--ready")
        dead = subprocess.Popen(["true"])
        dead.wait()
        half = {**self.arm(), "state": "closing", "closer": dead.pid, "claimed": time.time(), "did": ["ready"], "ready_error": "",
                "outcome": {"kind": "green", "what": "pipeline green", "at": T0 + 60, "link": ""}}
        (self.dir / "repo!12.json").write_text(json.dumps(half))
        self.poll(T0 + 120)
        self.poll(T0 + 180)
        self.assertEqual(self.story().count("## repo!12 · "), 1)
        self.assertIn("DONE - pipeline green; MR ready\n", self.story())
        self.assertEqual((self.calls(), self.arm()), ([], None), "the MR was readied before the poller died: not again")

    def merged(self, **more):
        return self.mr("success", state="merged", merge_commit_sha=self.pushed, **more)

    def test_until_merged_waits_for_the_merge_then_the_targets_pipeline(self):
        self.add("--mr", MR, "--until", "merged", "--left", "nothing left")
        self.poll(T0 + 60, self.mr("success", merge_when_pipeline_succeeds=True, detailed_merge_status="not_approved"))
        self.assertEqual((self.arm()["phase"], self.story(), self.ops()), ("merge", "", ["add", "green"]))
        for n in (2, 3):
            self.poll(T0 + n * 60, self.mr("success", merge_when_pipeline_succeeds=True, detailed_merge_status="not_approved"))
        self.assertEqual(self.story(), f"# st\n> watch repo!12 · waiting for approval · {self.stamp(T0 + 120)}\n", "said once")
        git(self.origin, "update-ref", "refs/heads/main", self.pushed)
        self.poll(T0 + 240, self.merged())
        arm = self.arm()
        self.assertEqual((arm["phase"], arm["merged_sha"], arm["target"]), ("target", self.pushed, "main"))
        self.assertEqual((git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), git(self.repo, "rev-parse", "HEAD")),
                         ("main", self.pushed), "switched to the default branch and pulled")
        self.assertEqual(git(self.repo, "rev-parse", "feature"), git(self.repo, "rev-parse", "feature@{0}"),
                         "the close's doc commit was never pushed: the branch that holds it stays")
        self.poll(T0 + 300, [])
        self.poll(T0 + 360, [{"status": "success"}])
        self.assertEqual(self.calls()[-1], f"{self.repo}|api projects/g%2Fp/pipelines?ref=main&sha={self.pushed}")
        self.assertEqual(self.story().split("\n\n")[1], "\n".join([
            f"## repo!12 · {self.stamp(T0 + 360)}",
            f"Watch: merged; main pipeline green {self.stamp(T0 + 360, '%H:%M')} · sha {self.pushed[:7]} · 6 min · {MR}",
            "Next: none", "DONE - merged; main pipeline green; back on main, feature kept (it has commits the merge did not take)",
            "FOR ME - nothing new", "OPEN - nothing", "LEFT - nothing left", ""]))
        self.assertEqual(self.ops(), ["add", "green", "merged", "done"])

    def test_a_branch_the_merge_took_whole_is_deleted_and_a_red_target_reported(self):
        git(self.repo, "reset", "-q", "--hard", "@{u}")
        self.add("--mr", MR, "--until", "merged")
        git(self.origin, "update-ref", "refs/heads/main", self.pushed)
        self.poll(T0 + 60, self.merged())
        self.assertEqual(git(self.repo, "branch", "--format", "%(refname:short)"), "main")
        self.poll(T0 + 120, [{"status": "failed", "web_url": "https://gitlab.com/g/p/-/pipelines/9"}])
        self.assertIn("FOR ME - 1. merged; main pipeline red: https://gitlab.com/g/p/-/pipelines/9\n", self.story())
        self.assertEqual(self.ops(), ["add", "merged", "report", "done"], "a red target is his call: a fix there is a new MR")

    def test_a_tree_somebody_works_in_stays_on_its_branch(self):
        (self.tmp / "agents.json").write_text(json.dumps([
            {"sessionId": "a", "pid": os.getpid(), "cwd": str(self.repo), "name": "st-report", "status": "idle"},
            {"sessionId": "b", "pid": os.getpid(), "cwd": str(self.repo), "name": "next-slice", "status": "busy"}]))
        self.add("--mr", MR, "--until", "merged")
        self.poll(T0 + 60, self.merged())
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "feature")
        self.assertEqual(self.story(), f"# st\n> watch repo!12 · merged; {self.repo} left on feature: session next-slice "
                                       f"works there · {self.stamp(T0 + 60)}\n")

    def test_a_cancelled_auto_merge_and_a_conflict_end_the_watch_at_the_second_poll(self):
        for n, (more, what) in enumerate([({"merge_when_pipeline_succeeds": False}, "auto-merge cancelled"),
                                          ({"has_conflicts": True}, "merge blocked: conflict")]):
            name = self.add("--mr", MR, "--until", "merged")
            self.poll(T0 + 60, self.mr("success"))
            self.poll(T0 + 120, self.mr("success", **more))
            with self.subTest(what):
                self.assertEqual(self.arm(name)["answer"], what, "the moment between green and the merge looks the same")
                self.poll(T0 + 180, self.mr("success", **more))
                self.assertIn(f"FOR ME - 1. {what}: {MR}\n", self.story().split("## ")[-1])
        name = self.add("--mr", MR, "--until", "merged")
        self.poll(T0 + 60, self.mr("success", sha="b" * 40))
        self.poll(T0 + 120, self.mr("success", sha="b" * 40))
        for n in (3, 4):
            self.poll(T0 + n * 60, self.mr("success", sha="b" * 40, merge_when_pipeline_succeeds=False))
        self.assertIn("\nOPEN - auto-merge cancelled by a push\n", self.story().split("## ")[-1])


if __name__ == "__main__":
    unittest.main()
