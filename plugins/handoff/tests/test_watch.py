"""Run with `python3 -m unittest discover plugins/handoff/tests`. Each test gets its own config dir, a
sandbox repository whose HEAD is one doc commit ahead of `@{u}` (a close commits after its push), a
fake `glab` that answers hand-written JSON per call, stub `claude`, `tmux` and `caffeinate`, and a
clock it sets, so nothing reaches GitLab, a real session or the user's files. The tests of a launch
run a copy of the plugin's scripts beside a fake `flowkit-spawn` that answers what the test left it."""
import importlib.util
import json
import os
import re
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
PIPELINE = "https://gitlab.com/g/p/-/pipelines/7"
JOBS = [{"id": 31, "name": "rspec", "stage": "test", "web_url": "https://gitlab.com/g/p/-/jobs/31"},
        {"id": 32, "name": "lint", "stage": "test", "web_url": "https://gitlab.com/g/p/-/jobs/32"}]
SID = "11111111-1111-1111-1111-111111111111"
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
        self.script = WATCH
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
        return subprocess.run([sys.executable, str(self.script), *args], cwd=cwd or self.repo, capture_output=True, text=True,
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
        head = status and {"id": 7, "sha": pipeline_sha or sha, "status": status, "web_url": PIPELINE}
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

    def events(self, name=None):
        path = self.claude / "capacity" / "log.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [e for e in events if e["event"] == "watch" and name in (None, e["name"])]

    def ops(self, name=None):
        return [e["op"] for e in self.events(name)]

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

        def ended():  # the pidfile goes a moment before the process does
            try:
                os.kill(pid, 0)
            except OSError:
                return True

        self.until(ended, "the poller's process to end")

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

    def test_status_json_lists_an_arm_from_its_add_to_its_end(self):
        def listed():
            data = json.loads((self.claude / "capacity" / "status.json").read_text())
            return [(w["name"], w["story"], w["state"], w["sha"][:7]) for w in data["watches"]]
        self.add()
        self.assertEqual(listed(), [("repo!12", "st", "pending", self.pushed[:7])])
        self.add("--check", "exit 2", "--name", "smoke")
        self.poll(T0 + 60, self.mr("running"))
        self.assertEqual([name for name, *_ in listed()], ["repo!12", "smoke"])
        self.poll(T0 + 120, self.mr("success"), name="repo!12")
        self.assertEqual([name for name, *_ in listed()], ["smoke"], "gone with its section: a board reads no stale watch")
        self.assertEqual(self.watch("drop", "smoke", at=T0 + 180).stdout, "dropped smoke\n")
        self.assertEqual(listed(), [])

    def test_the_ends_that_need_the_user(self):
        link = "https://gitlab.com/g/p/-/pipelines/7"
        for n, (answer, what, where, op) in enumerate([
                (self.mr("canceled"), "pipeline canceled", link, "report"),
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


class Stored(Watch):
    def test_add_keeps_the_model_effort_and_needs_of_the_registering_sessions_prompt(self):
        self.add("--check", "exit 2", "--name", "by-hand")
        by_hand = self.arm("by-hand")
        self.assertEqual((by_hand["model"], by_hand["effort"], by_hand["needs"]), ("", "", []), "a session started by hand")
        (self.claude / "sessions").mkdir()
        (self.claude / "sessions" / "999999.json").write_text(json.dumps({"sessionId": SID, "name": "st-s1"}))
        store = self.claude / "spawned"
        store.mkdir()
        head = "Repo: /Users/me/repo   Session: st-s1   Story: st\n"
        (store / "st-s1-20261116-101500.md").write_text(head + "Model: opus   Effort: high\n\nToday: x\n")
        (store / "st-s1-20261117-101500.md").write_text(head + "Model: fable   Effort: max\nNeeds: simulator, backend\nLeft: 0\n\n"
                                                         "Model: not-in-the-header\n")
        (store / "st-s10-20261118-101500.md").write_text(head + "Model: sonnet\n\nToday: x\n")
        self.add()
        arm = self.arm()
        self.assertEqual((arm["model"], arm["effort"], arm["needs"], arm["by"]["name"], arm["repo"]),
                         ("fable", "max", ["backend", "simulator"], "st-s1", str(self.repo)), "its newest stamped copy")
        (store / "st-s1.md").write_text(head + "Effort: xhigh\n\nToday: x\n")
        self.add("--check", "exit 2", "--name", "probe")
        probe = self.arm("probe")
        self.assertEqual((probe["model"], probe["effort"], probe["needs"]), ("", "xhigh", []), "the saved prompt, <name>.md")

    def test_on_green_takes_a_command_and_a_prompt(self):
        prompt = self.tmp / "st-pin.md"
        prompt.write_text("Repo: /Users/me/backend   Session: st-pin\n\nToday: pin the build.\n")
        other = self.tmp / "backend"
        git(self.tmp, "init", "-q", str(other))
        self.add("--mr", MR, "--on-green-run", "echo moved", "--on-green", str(prompt), "--repo", str(other))
        self.assertEqual(self.arm()["on_green"], {"run": "echo moved", "prompt": str(prompt), "repo": str(other), "name": "st-pin"},
                         "the session is named after the prompt file")
        self.add("--check", "exit 2", "--name", "build", "--on-green", str(prompt), "--on-green-name", "st-pin-2")
        on = self.arm("build")["on_green"]
        self.assertEqual((on["repo"], on["name"], on["run"]), (str(self.repo), "st-pin-2", ""), "--name is the watch's")
        for args, why in [(("--on-green", str(self.tmp / "missing.md")), "is missing or empty"),
                          (("--repo", str(other)), "--repo and --on-green-name say where"),
                          (("--on-green", str(prompt), "--on-green-name", "a b"), "a session's name is letters"),
                          (("--on-green", str(prompt), "--repo", str(self.tmp / "nowhere")), "is no directory")]:
            out = self.watch("add", "--story", "st", "--mr", MR, *args)
            self.assertEqual(out.returncode, 1, args)
            self.assertIn(why, out.stderr)


class Launches(Watch):
    """A copy of the plugin's scripts beside a fake flowkit-spawn, which writes down how it was
    called and answers what `spawns` left it. A launch is a detached wrapper's: wait for its trace."""

    def setUp(self):
        super().setUp()
        plugin = self.tmp / "plugin"
        shutil.copytree(PLUGIN / "scripts", plugin / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
        (plugin / "bin").mkdir()
        fake = plugin / "bin" / "flowkit-spawn"
        fake.write_text(f'#!/bin/sh\nd="{self.tmp}"\necho "$PWD|${{CLAUDE_PID:-no CLAUDE_PID}}|$*" >> "$d/spawn.calls"\n'
                        '[ -f "$d/spawn.out" ] && cat "$d/spawn.out"\ncode=0; [ -f "$d/spawn.exit" ] && read code < "$d/spawn.exit"\n'
                        'exit "$code"\n')
        fake.chmod(0o755)
        self.script = plugin / "scripts" / "watch.py"
        self.spawns("SPAWNED pane=1 id=%1 tab=st story=st name=st-fix-1 rc=off prompt=- created=0 try=1")

    def spawns(self, out="", code=0):
        (self.tmp / "spawn.out").write_text(f"{out}\n" if out else "")
        (self.tmp / "spawn.exit").write_text(f"{code}\n")

    def spawned(self):
        path = self.tmp / "spawn.calls"
        return path.read_text().splitlines() if path.exists() else []

    def session(self, header="Model: fable   Effort: xhigh\nNeeds: none"):
        """The registering session st-s1, as Claude Code and flowkit-spawn leave it: its sessions
        file, its prompt in the store, and its close in the story file."""
        for d in ("sessions", "spawned", "stories"):
            (self.claude / d).mkdir(exist_ok=True)
        (self.claude / "sessions" / "999999.json").write_text(json.dumps({"sessionId": SID, "name": "st-s1", "cwd": str(self.repo)}))
        (self.claude / "spawned" / "st-s1.md").write_text(
            f"Repo: /Users/me/repo   Session: st-s1   Story: st\n{header}\nLeft: 0\n\nToday: the slice.\n")
        (self.claude / "stories" / "st.md").write_text(
            "# st\nChain end: merge — the last slice sets auto-merge\n\n## st-s1 · 2026-11-17 10:00\n"
            f"Session: {SID}\nNext: none\nDONE - pushed the branch, MR ready\nFOR ME - nothing new\nOPEN - nothing\nLEFT - nothing left\n")

    def red(self, at=T0 + 60, name=None, jobs=JOBS, **mr):
        """One poll that finds the pipeline failed; the wrapper's brief then asks for the failed jobs."""
        self.poll(at, self.mr("failed", **mr), jobs, name=name)

    def section(self, name="repo!12"):
        """The arm's section once it is written, without its heading's name."""
        self.until(lambda: f"\n## {name} · " in self.story(), f"the section of {name}", 30)
        self.until(lambda: (self.arm(name) or {"state": ""})["state"] != "closing"
                   and ((self.arm(name) or {}).get("fix") or {"state": ""})["state"] != "launching", f"the end of {name}'s wrapper", 30)
        return self.story().split(f"\n## {name} · ")[-1].split("\n## ")[0].rstrip("\n").splitlines()

    def fix_closes(self, tree, at):
        """What a fix slice does last: a commit on top, pushed to the MR's branch, the watch armed again from its tree."""
        git(tree, "commit", "-q", "--allow-empty", "-m", "fix")
        git(tree, "push", "-q", "origin", "HEAD:refs/heads/feature")
        return self.add(at=at, cwd=tree), git(self.repo, "rev-parse", "refs/remotes/origin/feature")

    def clock(self, t):
        return self.stamp(t, "%H:%M")

    def module(self):
        spec = importlib.util.spec_from_file_location("flowkit_watch", self.script)
        watch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(watch)
        return watch


class Trees(Launches):
    def planted(self, why):
        """The fix slice's worktree: detached at origin/feature, where flowkit-spawn was started."""
        self.section()
        path = self.repo / ".claude" / "worktrees" / "st-fix-1"
        fix = self.arm()["fix"]
        self.assertEqual((fix["tree"], fix["worktree"], fix["state"]), (str(path), str(path), "started"))
        self.assertRegex(fix["why"], why)
        self.assertEqual((git(path, "rev-parse", "HEAD"), git(path, "rev-parse", "--abbrev-ref", "HEAD")), (self.pushed, "HEAD"))
        self.assertTrue(self.spawned()[0].startswith(f"{path}|no CLAUDE_PID|--from-watch repo!12 --story st --repo {path} --name st-fix-1 "))
        brief = (self.claude / "spawned" / "st-fix-1.md").read_text()
        self.assertRegex(brief, rf"detached at origin/feature, because {why}\.")
        self.assertIn("`git push origin HEAD:refs/heads/feature`", brief)
        return path

    def test_a_free_tree_still_on_the_branch_takes_the_fix(self):
        self.session()
        self.add()
        self.red()
        self.section()
        fix = self.arm()["fix"]
        self.assertEqual((fix["tree"], fix["worktree"], fix["why"]), (str(self.repo), "", ""))
        self.assertEqual(self.spawned(), [
            f"{self.repo}|no CLAUDE_PID|--from-watch repo!12 --story st --repo {self.repo} --name st-fix-1 --model fable "
            f"--effort xhigh {self.claude / 'spawned' / 'st-fix-1.md'}"], "in the tree, as nobody's session")
        brief = (self.claude / "spawned" / "st-fix-1.md").read_text()
        self.assertIn("The tree is on feature, as the slice that pushed left it", brief)
        self.assertIn("`git push`, then `/handoff:close`", brief)
        self.assertFalse((self.repo / ".claude").exists())

    def test_a_dirty_tree_gets_a_worktree_that_goes_with_the_next_arms_green(self):
        self.session()
        self.add()
        (self.repo / "work.txt").write_text("not committed\n")
        self.red()
        path = self.planted("the tree has uncommitted changes")
        name, sha = self.fix_closes(path, T0 + 600)
        arm = self.arm(name)
        self.assertEqual((name, arm["tree"], arm["branch"], arm["sha"], arm["worktree"]),
                         ("repo!12#2", str(path), "feature", sha, str(path)), "armed from a detached tree: what it pushed")
        self.assertIsNone(self.arm(), "the red arm went with the fix's close")
        self.assertEqual([(e["op"], e.get("end")) for e in self.events("repo!12")][-1], ("done", "armed"))
        self.poll(T0 + 660, self.mr("success", sha=sha), name=name)
        self.assertIn("DONE - pipeline green; the fix worktree removed\n", self.story())
        self.assertFalse(path.exists())
        self.assertEqual(len(git(self.repo, "worktree", "list").splitlines()), 1)

    def test_a_tree_moved_on_gets_a_worktree_kept_while_it_holds_changes(self):
        self.session()
        self.add()
        git(self.repo, "checkout", "-q", "main")
        self.red()
        path = self.planted("the tree is on another branch")
        name, sha = self.fix_closes(path, T0 + 600)
        (path / "left.txt").write_text("nobody committed this\n")
        self.poll(T0 + 660, self.mr("failed", sha=sha), JOBS, name=name)
        last = self.story().split("\n## ")[-1]
        self.assertIn("\nWatch: red again after one fix: rspec, lint ", last)
        self.assertRegex(last, rf"\nDONE - worktree {re.escape(str(path))} kept: .*\n")
        self.assertTrue(path.exists(), "a second red ends the arm too; the worktree stays only for what it holds")

    def test_a_tree_somebody_works_in_gets_a_worktree_though_the_branch_is_checked_out_there(self):
        self.session()
        self.add()
        (self.tmp / "agents.json").write_text(json.dumps([{"sessionId": "2" * 8 + SID[8:], "name": "other", "cwd": str(self.repo),
                                                           "status": "busy", "pid": os.getpid()}]))
        self.red()
        self.planted("session other works there")
        self.assertEqual(git(self.repo, "rev-parse", "--abbrev-ref", "HEAD"), "feature")

    def test_what_takes_a_tree(self):
        conf = self.tmp / "xdg" / "flowkit" / "capacity.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text(f"[{self.repo}]\nresource simulator = a device check\n")
        state, tree = self.claude / "capacity", str(self.repo)
        for d in ("queue", "launching", "resources"):
            (state / d).mkdir(parents=True)
        (self.claude / "stories").mkdir()
        (self.claude / "stories" / "st.md").write_text(f"# st\n\n## st-s1 · 2026-11-17 10:00\nSession: {SID}\nNext: none\n")
        rows = [{"sessionId": SID, "name": "st-s1", "cwd": tree, "status": "idle", "pid": os.getpid()},
                {"sessionId": "3" * 8 + SID[8:], "name": "st-report", "cwd": tree, "status": "busy", "pid": os.getpid()}]
        (self.tmp / "agents.json").write_text(json.dumps(rows))
        with mock.patch.dict(os.environ, self.env):
            taken = self.module().taken
            self.assertEqual(taken(tree), "", "a reporter and a slice whose close is recorded are nobody working")
            os.mkfifo(self.tmp / "fifo")  # nobody's children: a shell that waits, then a server
            shell = int(subprocess.run(["sh", "-c", f"sh -c 'read x < {self.tmp}/fifo' >/dev/null 2>&1 & echo $!"], cwd=tree,
                                       capture_output=True, text=True).stdout)
            self.addCleanup(lambda: subprocess.run(["kill", str(shell)], capture_output=True))
            self.assertEqual(taken(tree), "", "nor is a bare shell")
            server = int(subprocess.run(["sh", "-c", "sleep 30 >/dev/null 2>&1 & echo $!"], cwd=tree, capture_output=True,
                                        text=True).stdout)
            self.addCleanup(lambda: subprocess.run(["kill", str(server)], capture_output=True))
            self.assertEqual(taken(tree), f"sleep (pid {server}) runs there")
            (state / "resources" / "simulator.json").write_text(json.dumps({"session": "me", "until": time.time() + 600}))
            self.assertEqual(taken(tree), "simulator is held for the user's testing")
            (state / "launching" / f"{server}.json").write_text(json.dumps({"tree": tree, "name": "st-s2"}))
            self.assertEqual(taken(tree), "st-s2 is starting there")
            (state / "queue" / "1.json").write_text(json.dumps({"tree": tree, "name": "st-s3"}))
            self.assertEqual(taken(tree), "st-s3 is queued for it")
            (self.tmp / "agents.json").write_text(json.dumps(rows + [{**rows[0], "sessionId": "4" * 8 + SID[8:], "name": "by-hand"}]))
            self.assertEqual(taken(tree), "session by-hand works there")
            (self.tmp / "agents.json").write_text("not json")
            self.assertEqual(taken(tree), "the sessions are unknown (`claude agents` did not answer)")

    def test_one_checkout_takes_the_fix_through_the_gate_and_never_a_worktree(self):
        conf = self.tmp / "xdg" / "flowkit" / "capacity.conf"
        conf.parent.mkdir(parents=True)
        conf.write_text(f"[{self.repo}]\ncheckouts = 1\n")
        self.session()
        self.add()
        held = self.claude / "capacity" / "resources" / "repo-checkout.json"
        held.parent.mkdir(parents=True)
        held.write_text(json.dumps({"session": "5" * 8 + SID[8:], "name": "st-s2"}))
        (self.repo / "work.txt").write_text("the running slice's\n")
        self.spawns("QUEUED #1, ~10:15 · id=3 · busy: repo-checkout is held by st-s2 since 10:00", 11)
        self.red()
        lines = self.section()
        fix = self.arm()["fix"]
        self.assertEqual((fix["tree"], fix["worktree"], fix["why"], fix["state"]), (str(self.repo), "", "checkout", "queued"))
        self.assertEqual(lines[2:4], ["Next: queued st-fix-1", f"DONE - fix slice st-fix-1 queued (auto) for {self.repo}: "
                                      "busy: repo-checkout is held by st-s2 since 10:00"])
        self.assertIn("This repository has one checkout", (self.claude / "spawned" / "st-fix-1.md").read_text())
        self.assertFalse((self.repo / ".claude").exists())
        # a session there that holds no lease is the user's own: nothing starts under it
        held.unlink()
        (self.repo / "work.txt").unlink()
        (self.tmp / "agents.json").write_text(json.dumps([{"sessionId": "6" * 8 + SID[8:], "name": "by-hand", "cwd": str(self.repo),
                                                           "status": "busy", "pid": os.getpid()}]))
        other = "https://gitlab.com/g/p/-/merge_requests/13"
        self.add("--mr", other)
        self.red(name="repo!13")
        self.assertEqual(self.section("repo!13")[4], "FOR ME - 1. pipeline red; no fix slice started: session by-hand works there, in the "
                         f"repository's one checkout, without holding repo-checkout: {PIPELINE}")
        self.assertEqual(len(self.spawned()), 1)

    def test_a_worktree_git_cannot_make_is_a_report(self):
        self.session()
        self.add()
        (self.repo / "work.txt").write_text("not committed\n")
        (self.repo / ".claude" / "worktrees").mkdir(parents=True)
        (self.repo / ".claude" / "worktrees" / "st-fix-1").write_text("in the way\n")
        self.red()
        lines = self.section()
        self.assertEqual(lines[2], "Next: none")
        self.assertRegex(lines[4], r"^FOR ME - 1\. pipeline red; no fix slice started: the tree has uncommitted changes, and git "
                         rf"worktree add failed: .+: {re.escape(PIPELINE)}$")
        self.assertEqual((self.arm(), self.spawned(), self.ops()), (None, [], ["add", "red", "fix", "report", "done"]))


class Launch(Launches):
    def test_spawned_is_the_reds_section_and_the_arm_stays_fixing(self):
        self.session("Model: fable   Effort: max\nNeeds: simulator")
        self.add()
        self.red()
        self.assertEqual(self.section(), [
            self.stamp(T0 + 60), f"Watch: pipeline red {self.clock(T0 + 60)} · sha {self.pushed[:7]} · 1 min · {MR}",
            "Next: started st-fix-1", f"DONE - fix slice st-fix-1 started (auto, effort xhigh for max) in {self.repo}",
            "FOR ME - nothing new", f"OPEN - pipeline red: {PIPELINE}", "LEFT - nothing left"])
        arm = self.arm()
        self.assertEqual((arm["state"], arm["fix"]["state"], arm["next"], self.ops()), ("fixing", "started", "started st-fix-1", ["add", "red", "fix"]))
        self.assertIn("--model fable --effort xhigh ", self.spawned()[0], "max is nobody's to give a launch no one approved")
        prompt = self.claude / "spawned" / "st-fix-1.md"
        brief = prompt.read_text()
        self.assertTrue(brief.startswith(
            f"Repo: {self.repo}   Session: st-fix-1   Story: st\n"
            "Model: fable   Effort: xhigh (auto: max is capped for a launch nobody approved)\n"
            "Launched-by: watch repo!12, registered by st-s1\nNeeds: none\nLeft: 0\n\n"), brief)
        for line in (f"{MR} is red at {self.pushed[:12]}: pipeline red ({PIPELINE}).",
                     "- rspec (test, job 31): https://gitlab.com/g/p/-/jobs/31\n- lint (test, job 32): https://gitlab.com/g/p/-/jobs/32\n",
                     "`glab ci trace <job id> -R g/p`", "never a rebase, an amend or a force-push", "Red that main has already",
                     "The slice that pushed held simulator", "DONE - pushed the branch, MR ready\n",
                     "set auto-merge again after your push", "\nChain end: merge — the last slice sets auto-merge\n"):
            self.assertIn(line, brief)
        self.assertNotIn("{", brief, "every word is filled in")
        self.assertEqual(self.calls()[1], f"{self.repo}|api projects/g%2Fp/pipelines/7/jobs?scope[]=failed&per_page=100")
        self.assertIn("SPAWNED pane=1", (self.dir / "launched" / "st-fix-1.log").read_text())
        # no deadline runs while the fix slice works, and nothing is asked
        self.poll(T0 + 7 * 3600)
        self.assertEqual((self.arm()["state"], len(self.calls()), self.story().count("\n## repo!12")), ("fixing", 2, 1))

    def test_the_poller_keeps_nothing_awake_for_an_arm_that_only_waits_for_its_fix_slice(self):
        self.session()
        self.add()
        self.red()
        self.section()
        beat = self.dir / "poller.pid"
        beat.unlink()
        poller = subprocess.Popen([sys.executable, str(self.script), "run"], cwd=self.tmp, env={**self.env, "FLOWKIT_WATCH_POLL": "0.2"})
        self.addCleanup(poller.wait)
        self.addCleanup(poller.terminate)
        self.until(beat.exists, "the poller")
        for _ in range(3):
            last = beat.stat().st_mtime
            self.until(lambda: beat.stat().st_mtime > last, "its next beat")
        self.assertFalse((self.tmp / "caffeinate.calls").exists(), "a fix slice can take hours, or wait for the user")
        self.add("--check", "exit 2", "--name", "probe")
        self.until((self.tmp / "caffeinate.calls").exists, "the keep-awake, once an arm is asked")

    def test_the_exits_that_start_nothing_are_reports(self):
        self.session()
        for n, (code, said, why) in enumerate([
                (4, "a session named 'st-fix-1' is already running (claude agents); pass --force to spawn another", "a session of that name runs"),
                (6, "repo has uncommitted changes; commit them first (or --force):\n M work.txt", "the tree has uncommitted changes"),
                (10, "session 'st-fix-3' exited twice at start-up in pane %3; its output is in the pane", "claude exited twice at start-up")], 1):
            name = self.add("--mr", f"https://gitlab.com/g/p/-/merge_requests/2{n}")
            self.spawns(said, code)
            self.red(name=name)
            with self.subTest(code):
                lines = self.section(name)
                what = (f"pipeline red; the fix slice did not start (flowkit-spawn exit {code}, {why}; its output: "
                        f"{self.dir / 'launched' / f'st-fix-{n}.log'})")
                self.assertEqual(lines[2:6], ["Next: none", "DONE - nothing new", f"FOR ME - 1. {what}: {PIPELINE}", f"OPEN - {what}"])
                self.assertEqual((self.arm(name), self.ops(name)), (None, ["add", "red", "fix", "report", "done"]))
        self.assertEqual(len(self.spawned()), 3)

    def test_a_launch_its_wrapper_left_is_made_again_and_never_twice(self):
        self.session()
        self.add()
        dead = subprocess.Popen(["true"])
        dead.wait()
        arm = self.arm()
        half = {**arm, "state": "fixing", "closer": None, "claimed": time.time(), "did": ["launching"],
                "red": {"what": "pipeline red", "link": PIPELINE, "pipeline": 7, "at": T0 + 60},
                "fix": {"name": "st-fix-1", "state": "launching", "at": T0 + 60, "merge": ""}}
        (self.dir / "repo!12.json").write_text(json.dumps(half))
        self.poll(T0 + 120)
        self.assertEqual((self.arm()["rev"], self.spawned()), (arm["rev"], []), "its wrapper is starting: left alone")
        (self.dir / "repo!12.json").write_text(json.dumps({**half, "closer": dead.pid}))
        self.spawns("a session named 'st-fix-1' is already running (claude agents); pass --force to spawn another", 4)
        self.poll(T0 + 180, JOBS)
        self.assertEqual(self.section()[2], "Next: started st-fix-1", "the wrapper that went had started it")
        self.assertEqual((len(self.spawned()), self.arm()["fix"]["state"]), (1, "started"))


class Red(Launches):
    def test_a_second_red_is_reported_and_starts_nothing(self):
        self.session()
        self.add("--mr", MR, "--ready", "--on-green-run", "echo moved")
        self.red()
        self.section()
        name, sha = self.fix_closes(self.repo, T0 + 600)
        arm = self.arm(name)
        self.assertEqual((name, arm["sha"], arm["ready"], arm["on_green"]["run"], self.arm()),
                         ("repo!12#2", sha, True, "echo moved", None), "the next arm keeps what the first close asked for")
        name, sha = self.fix_closes(self.repo, T0 + 600)
        arm = self.arm(name)
        self.assertEqual((name, arm["sha"], arm["ready"], arm["on_green"]["run"]), ("repo!12#2", sha, True, "echo moved"),
                         "pushed once more: the same arm, and it still keeps them")
        self.poll(T0 + 660, self.mr("failed", sha=sha), JOBS, name=name)
        self.assertEqual(self.story().split("\n## ")[-1].splitlines()[1:6], [
            f"Watch: red again after one fix: rspec, lint {self.clock(T0 + 660)} · sha {sha[:7]} · 1 min · {MR}", "Next: none",
            "DONE - nothing new", f"FOR ME - 1. red again after one fix: rspec, lint: {PIPELINE}",
            "OPEN - red again after one fix: rspec, lint"])
        self.assertEqual((len(self.spawned()), self.ops(name), self.arm(name)), (1, ["add", "arm", "red", "done"], None))

    def test_a_fix_slice_that_closes_without_arming_or_is_gone_ends_the_arm(self):
        self.session()
        self.add()
        self.red()
        self.section()
        with (self.claude / "stories" / "st.md").open("a") as f:
            f.write(f"\n## st-fix-1 · 2026-11-17 12:00\nSession: {'7' * 8 + SID[8:]}\nNext: none\nFOR ME - 1. red on main already\n")
        self.poll(T0 + 120)
        self.assertIsNone(self.arm())
        self.assertIn(f"\n> watch repo!12 · st-fix-1 closed without arming the watch again · {self.stamp(T0 + 120)}\n", self.story())
        self.assertEqual([(e["op"], e.get("end")) for e in self.events()][-1], ("done", "closed"))
        other = self.add("--mr", "https://gitlab.com/g/p/-/merge_requests/13")
        self.red(name=other)
        self.section(other)
        row = {"sessionId": "8" * 8 + SID[8:], "name": "st-fix-2", "cwd": str(self.repo), "status": "busy", "pid": os.getpid()}
        (self.tmp / "agents.json").write_text(json.dumps([row]))
        self.poll(T0 + 60 + 1200, name=other)
        self.assertEqual(self.arm(other)["state"], "fixing", "its session runs")
        (self.tmp / "agents.json").write_text("[]")
        self.poll(T0 + 60 + 1260, name=other)
        self.assertEqual(self.arm(other)["state"], "fixing", "asked for every tenth minute")
        self.poll(T0 + 60 + 1800, name=other)
        self.assertIsNone(self.arm(other))
        self.assertIn(f"\n> watch repo!13 · st-fix-2 is gone with no close · {self.stamp(T0 + 60 + 1800)}\n", self.story())

    def dependent(self):
        """An MR of branch dep, in a worktree of its own, whose target is the first MR's branch."""
        dep = self.tmp / "dep"
        git(self.repo, "worktree", "add", "-q", "-b", "dep", str(dep))
        git(dep, "commit", "-q", "--allow-empty", "-m", "dep work")
        git(dep, "push", "-q", "-u", "origin", "dep")
        self.assertEqual(self.add("--mr", "https://gitlab.com/g/p/-/merge_requests/13", cwd=dep), "repo!13")
        return dep, git(dep, "rev-parse", "HEAD")

    def test_a_red_base_is_waited_for_and_its_green_merges_the_base_in(self):
        self.session()
        dep, head = self.dependent()
        self.add()
        self.red(name="repo!12")
        self.section()
        self.poll(T0 + 120, self.mr("failed", sha=head, target_branch="feature"), name="repo!13")
        arm = self.arm("repo!13")
        self.assertEqual((arm["state"], arm["waits"], self.ops("repo!13"), len(self.spawned())),
                         ("waiting-base", {"base": "repo!12", "story": "st", "branch": "feature"}, ["add", "red"], 1))
        self.assertIn(f"\n> watch repo!13 · pipeline red; waiting for its base repo!12, which is red · {self.stamp(T0 + 120)}\n", self.story())
        self.poll(T0 + 7 * 3600, name="repo!13")
        self.assertEqual((self.arm("repo!13")["state"], len(self.calls())), ("waiting-base", 3), "no deadline, nothing asked")
        name, sha = self.fix_closes(self.repo, T0 + 7 * 3600)
        self.poll(T0 + 7 * 3600 + 30, name="repo!13")
        self.assertEqual(self.arm("repo!13")["state"], "waiting-base", "the base's next arm is open")
        self.poll(T0 + 7 * 3600 + 60, self.mr("success", sha=sha), name=name)
        self.poll(T0 + 7 * 3600 + 120, JOBS, name="repo!13")
        self.assertEqual(self.section("repo!13")[2:4], ["Next: started st-fix-2", f"DONE - merge slice st-fix-2 started (auto) in {dep}"])
        self.assertTrue(self.spawned()[1].startswith(f"{dep}|no CLAUDE_PID|--from-watch repo!13 --story st --repo {dep} --name st-fix-2 "))
        brief = (self.claude / "spawned" / "st-fix-2.md").read_text()
        self.assertIn("while its base, feature, was red", brief)
        self.assertIn("`git fetch origin feature`, then `git merge origin/feature`", brief)
        self.assertNotIn("Failed jobs", brief)
        self.assertEqual(self.ops("repo!13"), ["add", "red", "fix"])

    def test_a_base_that_stays_red_reports_its_dependent(self):
        self.session()
        _, head = self.dependent()
        self.add()
        self.red(name="repo!12")
        self.section()
        self.poll(T0 + 120, self.mr("failed", sha=head, target_branch="feature"), name="repo!13")
        name, sha = self.fix_closes(self.repo, T0 + 600)
        self.poll(T0 + 660, self.mr("failed", sha=sha), JOBS, name=name)
        self.poll(T0 + 720, name="repo!13")
        self.assertIn(f"\nFOR ME - 1. pipeline red; its base repo!12 ended with no green: {PIPELINE}\n", self.story())
        self.assertEqual((len(self.spawned()), self.ops("repo!13"), self.arm("repo!13")), (1, ["add", "red", "report", "done"], None))


class OnGreen(Launches):
    def pin(self, header="Model: opus   Effort: max"):
        prompt, other = self.tmp / "st-pin.md", self.tmp / "backend"
        prompt.write_text(f"Repo: /Users/me/backend   Session: st-pin   Story: st\n{header}\n\nToday: pin the build.\n")
        git(self.tmp, "init", "-q", str(other))
        return prompt, other

    def test_the_command_runs_first_then_the_prompt_starts(self):
        self.session()
        prompt, other = self.pin()
        run = f'test ! -e {self.tmp}/spawn.calls && echo "$FLOWKIT_WATCH_NAME ran in $PWD before the launch"'
        self.add("--mr", MR, "--on-green-run", run, "--on-green", str(prompt), "--repo", str(other))
        self.poll(T0 + 60, self.mr("success"))
        self.assertEqual(self.section()[2:5], [
            "Next: started st-pin", f"DONE - pipeline green; `{run}` exit 0: repo!12 ran in {self.repo} before the launch; "
            "started st-pin (auto, effort xhigh for max)", "FOR ME - nothing new"])
        self.assertEqual(self.spawned(), [f"{other}|no CLAUDE_PID|--from-watch repo!12 --story st --repo {other} --name st-pin "
                                          f"--model opus --effort xhigh {prompt}"], "the prompt's own model, its effort capped")
        self.assertEqual((self.arm(), self.ops(), self.events()[-1]["next"]), (None, ["add", "green", "done"], "started st-pin"))

    def test_a_command_that_fails_and_a_prompt_that_does_not_start_are_for_the_user(self):
        self.session()
        prompt, other = self.pin("Needs: none")
        run = "echo no such state >&2; exit 3"
        self.add("--mr", MR, "--on-green-run", run, "--on-green", str(prompt), "--repo", str(other))
        self.spawns("repo has uncommitted changes; commit them first (or --force):", 6)
        self.poll(T0 + 60, self.mr("success"))
        self.assertEqual(self.section()[2:7], [
            "Next: none", f"DONE - pipeline green; `{run}` exit 3: no such state", "FOR ME",
            f"1. the on-green command failed, see DONE: `{run}`",
            "2. start st-pin, the watch could not (flowkit-spawn exit 6, the tree has uncommitted changes; its output: "
            f"{self.dir / 'launched' / 'st-pin.log'}): `flowkit-spawn --story st --repo {other} --name st-pin {prompt}`"])
        self.assertIn(f"--name st-pin --model fable --effort xhigh {prompt}", self.spawned()[0], "the registering slice's model")
        name = self.add("--check", "exit 0", "--name", "build", "--on-green", str(prompt), "--repo", str(other))
        self.spawns("QUEUED #1, ~10:15 · id=3 · busy: backend is held by st-s9 since 10:00", 11)
        self.poll(T0 + 120, name=name)
        self.assertEqual(self.section(name)[2:4], ["Next: queued st-pin", "DONE - check green; queued st-pin (auto): "
                                                   "busy: backend is held by st-s9 since 10:00"])

    def test_a_command_alone_is_run_by_the_poll_itself(self):
        name = self.add("--check", "exit 0", "--name", "build", "--on-green-run", "echo pinned")
        self.poll(T0 + 60)
        self.assertIn("\nDONE - check green; `echo pinned` exit 0: pinned\n", self.story())
        self.assertEqual((self.arm(name), self.spawned()), (None, []))

    def test_the_test_queue_line_of_the_mr_moves(self):
        self.session()
        stories, log = self.claude / "stories", self.claude / "capacity" / "log.jsonl"
        (stories / "epic.tests.md").write_text(f"# epic\nlogin · {MR} · simulator · planned\n"
                                               "other · https://gitlab.com/g/p/-/merge_requests/99 · emulator · planned\n")
        (stories / "st.tests.md").write_text("cart · !12 · simulator · planned\n")
        log.parent.mkdir(parents=True)
        log.write_text("".join(json.dumps({"t": T0 - n, "event": "watch", "op": "green", "name": f"repo!{n}", "story": "old",
                                           "repo": str(self.repo), "minutes": n}) + "\n" for n in (10, 20, 60)))

        def lines():
            return (stories / "epic.tests.md").read_text().splitlines()[1:] + (stories / "st.tests.md").read_text().splitlines()

        self.add()
        eta = self.clock(T0 + 20 * 60)
        self.assertEqual(lines(), [f"login · {MR} · simulator · building · {eta}",
                                   "other · https://gitlab.com/g/p/-/merge_requests/99 · emulator · planned",
                                   f"cart · !12 · simulator · building · {eta}"], "the ETA is the repository's median green")
        self.red()
        self.section()
        self.assertEqual(lines()[::2], [f"login · {MR} · simulator · fixing", "cart · !12 · simulator · fixing"])
        name, sha = self.fix_closes(self.repo, T0 + 600)
        self.assertEqual(lines()[0], f"login · {MR} · simulator · building · {self.clock(T0 + 600 + 20 * 60)}")
        self.poll(T0 + 660, self.mr("success", sha=sha), name=name)
        self.assertEqual(lines()[::2], [f"login · {MR} · simulator · ready to test", "cart · !12 · simulator · ready to test"])
