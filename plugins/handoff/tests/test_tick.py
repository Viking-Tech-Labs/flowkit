"""Run with `python3 -m unittest discover plugins/handoff/tests`. Loads flowkit-tick as a module with
its state in a folder of the test's own and its tmux job stubbed, so a pass reaches no tmux server,
no `claude agents` and none of the user's files. The last class runs the commands themselves, as a
tmux.conf and the timer do, against a `tmux` and a `claude` of the test's own."""
import contextlib
import fcntl
import importlib.machinery
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parent.parent
BIN = PLUGIN / "bin" / "flowkit-tick"
SEP = "\x1f"


def load(path=BIN):
    loader = importlib.machinery.SourceFileLoader("flowkit_tick", str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


class Tick(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for sub in ("flowkit/retry",):
            (self.tmp / sub).mkdir(parents=True)
        self.tk = load()
        self.tk.CLAUDE = self.tmp
        self.tk.CONFIG = self.tmp  # never the user's handoff.conf: with remind=on there a pass would start a real one
        self.tk.CACHE = self.tmp / "flowkit" / "tick.json"
        self.tk.marks = self.marks
        self.line, self.marked = "", 0

    def marks(self):
        self.marked += 1
        return self.line

    def kept(self):
        return json.loads(self.tk.CACHE.read_text())

    def printed(self, call, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            code = call(*args)
        return out.getvalue(), code


class Jobs(Tick):
    """What a pass does besides the marks: each job costs a read while there is nothing to do, and
    starts its work detached, no more often than it should."""

    def test_a_test_reads_its_own_handoff_conf(self):
        (self.tmp / "handoff.conf").write_text("remind=on\n")
        self.assertEqual(self.tk.setting("remind"), "on")

    def test_the_tick_drains_the_capacity_queue(self):
        queue = self.tmp / "capacity" / "queue"
        with mock.patch.object(self.tk.subprocess, "Popen") as popen:
            self.tk.tick()
            queue.mkdir(parents=True)
            (queue / ".seq").write_text("1\n")
            self.tk.tick()
            self.assertEqual(popen.call_count, 0, "an empty queue costs one directory read")
            (queue / "1.json").write_text("{}")
            self.tk.tick()
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(popen.call_args[0][0][1:], [str(PLUGIN / "scripts" / "capacity.py"), "drain"])
            self.tk.tick()
            self.assertEqual(popen.call_count, 1, "at most every FLOWKIT_CAPACITY_POLL seconds")
            with mock.patch.dict(os.environ, {"FLOWKIT_CAPACITY_POLL": "0"}):
                self.tk.tick()
            self.assertEqual(popen.call_count, 2)

    def test_the_tick_runs_the_minute_pass_while_a_stop_line_exists(self):
        conf = self.tmp / "capacity.conf"
        with mock.patch.object(self.tk.subprocess, "Popen") as popen:
            self.tk.tick()
            conf.write_text("[/Users/me/app]\nresource simulator = a device check\n# stop simulator = no\n")
            self.tk.tick()
            self.assertEqual(popen.call_count, 0, "no stop line costs one file read")
            conf.write_text("[/Users/me/app]\nresource simulator = a device check\nstop simulator = ./stop-devices\n")
            self.tk.tick()
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(popen.call_args[0][0][1:], [str(PLUGIN / "scripts" / "capacity.py"), "idle"])
            self.tk.tick()
            self.assertEqual(popen.call_count, 1, "at most once a minute")
            old = time.time() - 61
            os.utime(self.tmp / "capacity" / ".idle", (old, old))
            self.tk.tick()
            self.assertEqual(popen.call_count, 2)

    def test_the_installed_copy_works_through_the_installed_plugin(self):
        """The copy in ~/.local/bin is no plugin (the resolver the kit puts in ~/.local/scripts
        notwithstanding): a job starts the script of the newest handoff in installed_plugins.json
        that has one."""
        copy = self.tmp / "home" / ".local" / "bin" / "flowkit-tick"
        copy.parent.mkdir(parents=True)
        shutil.copyfile(BIN, copy)
        (copy.parent.parent / "scripts").mkdir()
        (copy.parent.parent / "scripts" / "capacity.py").write_text("# the kit's resolver for a 0.8.0 tick\n")
        self.tk = load(copy)
        self.tk.CLAUDE = self.tmp
        (self.tmp / "plugins").mkdir()
        (self.tmp / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {"handoff@flowkit": [
            {"installPath": str(self.tmp / "gone")}, {"installPath": str(PLUGIN)}]}}))
        queue = self.tmp / "capacity" / "queue"
        queue.mkdir(parents=True)
        (queue / "1.json").write_text("{}")
        with mock.patch.object(self.tk.subprocess, "Popen") as popen:
            self.tk.drain()
        self.assertEqual(popen.call_args[0][0][1:], [str(PLUGIN / "scripts" / "capacity.py"), "drain"])
        (self.tmp / "flowkit" / "watches").mkdir()
        (self.tmp / "flowkit" / "watches" / "repo!12.json").write_text("{}")
        with mock.patch.object(self.tk.subprocess, "Popen") as popen:
            self.tk.watch()
        self.assertEqual(popen.call_args[0][0][1:], [str(PLUGIN / "scripts" / "watch.py"), "run"])

    def test_the_tick_starts_a_watch_poller_that_went(self):
        state = self.tmp / "flowkit" / "watches"
        state.mkdir()
        gone = subprocess.Popen(["true"])
        gone.wait()
        with mock.patch.object(self.tk.subprocess, "Popen") as popen:
            self.tk.tick()
            self.assertEqual(popen.call_count, 0, "no watch is open: one directory read")
            (state / "repo!12.json").write_text("{}")
            (state / "poller.pid").write_text(str(os.getpid()))
            self.tk.watch()
            self.assertEqual(popen.call_count, 0, "its poller runs and beats")
            os.utime(state / "poller.pid", (0, 0))
            self.tk.tick()
            self.assertEqual(popen.call_args[0][0][1:], [str(PLUGIN / "scripts" / "watch.py"), "run"],
                             "a heartbeat older than three minutes")
            self.tk.watch()
            self.assertEqual(popen.call_count, 1, "at most once a minute")
            (state / "poller.pid").write_text(str(gone.pid))
            os.utime(state / ".tick", (0, 0))
            self.tk.watch()
            self.assertEqual(popen.call_count, 2, "a fresh heartbeat of a process that is gone")

    def test_the_tick_starts_a_limit_waiter_that_went(self):
        state = self.tmp / "flowkit" / "retry"
        sid = "00000001-0000-0000-0000-000000000000"
        gone = subprocess.Popen(["true"])
        gone.wait()
        rec = {"kind": "rate_limit", "resume": True, "at": 5.0, "pid": os.getpid()}

        def tick(**change):
            (state / f"{sid}.json").write_text(json.dumps({**rec, **change}))
            (state / ".tick").unlink(missing_ok=True)
            self.tk.limits()

        with mock.patch.object(self.tk.subprocess, "Popen") as popen:
            self.tk.tick()
            self.assertEqual(popen.call_count, 0, "no record: one directory read")
            (state / f"{sid}.json").write_text(json.dumps(rec))
            self.tk.tick()
            self.assertEqual(popen.call_args[0][0][1:], [str(PLUGIN / "scripts" / "handoff.py"), "retry-wait", sid])
            self.tk.limits()
            self.assertEqual(popen.call_count, 1, "at most once a minute")
            for change in ({"kind": "server_error"}, {"resume": False}, {"handled": 5.0, "state": "continued"},
                           {"handled": 5.0, "state": "unreachable"}, {"pid": gone.pid}, {"pid": None}):
                tick(**change)
                self.assertEqual(popen.call_count, 1, change)
            tick(handled=4.0)
            self.assertEqual(popen.call_count, 2, "a stop newer than the one its waiter was done with")
            with (state / f"{sid}.lock").open("w") as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                tick()
                self.assertEqual(popen.call_count, 2, "its waiter lives: it holds the lock")
            tick()
            self.assertEqual(popen.call_count, 3, "a lock file nobody holds is a dead waiter's")
            login = self.tmp / "login"
            login.mkdir()
            who = {"account": "acct-1", "organization": "org-1"}
            native = dict(handled=5.0, state="native", config=str(login), account=who)
            for org, count, why in (("org-1", 3, "the account it stopped on: still Claude Code's own to continue"),
                                    ("org-2", 4, "another organization under its login: open again")):
                (login / ".claude.json").write_text(json.dumps(
                    {"oauthAccount": {"accountUuid": "acct-1", "organizationUuid": org}}))
                tick(**native)
                self.assertEqual(popen.call_count, count, why)
            (login / ".claude.json").write_text("{}")
            tick(**native)
            self.assertEqual(popen.call_count, 4, "nobody logged in is no change")

    def test_the_tick_starts_the_reconnect_when_the_account_of_a_login_changes(self):
        state = self.tmp / "flowkit"
        wrote = [time.time_ns()]

        def login(path, account, org="org-1"):
            path.write_text(json.dumps({"oauthAccount": {"accountUuid": account, "organizationUuid": org}}) if account else "{}")
            wrote[0] += 10 ** 9
            os.utime(path, ns=(wrote[0], wrote[0]))

        def tick(minutes=2):
            if (state / ".remote-tick").exists():
                os.utime(state / ".remote-tick", (time.time() - minutes * 60,) * 2)
            self.tk.logins()

        own = self.tmp / ".claude.json"
        with mock.patch.object(self.tk.subprocess, "Popen") as popen:
            handoff = str(PLUGIN / "scripts" / "handoff.py")
            login(own, "acct-1")
            tick()
            login(own, "acct-1")
            tick()
            self.assertEqual(popen.call_count, 0, "the first sight, then the same account rewritten")
            login(own, "acct-2")
            tick()
            self.assertEqual(popen.call_args[0][0][1:], [handoff, "reconnect", "--all"])
            tick(minutes=0.5)
            self.assertEqual(popen.call_count, 1, "at most once a minute")
            tick()
            self.assertEqual((popen.call_count, popen.call_args[0][0][-1]), (2, "--all"), "its notice may come late")
            seen = json.loads((state / "logins.json").read_text())
            (state / "logins.json").write_text(json.dumps({**seen, "changed": time.time() - self.tk.LOOK - 1}))
            tick()
            self.assertEqual(popen.call_count, 2, "three minutes on, nobody owed: two stats and a directory read")
            (state / "remote").mkdir()
            (state / "remote" / "s1.owed").write_text("1")
            tick()
            self.assertEqual((popen.call_count, popen.call_args[0][0][1:]), (3, [handoff, "reconnect"]), "one is owed its link")
            (state / "remote" / "s1.owed").unlink()
            login(own, None)
            tick()
            login(own, "acct-2", "org-2")
            tick()
            self.assertEqual((popen.call_count, popen.call_args[0][0][-1]), (4, "--all"), "logged out, then another organization")
            home = self.tmp / "home"
            (home / ".claude").mkdir(parents=True)
            self.tk.CLAUDE = home / ".claude"
            with mock.patch.object(self.tk.Path, "home", return_value=home):
                login(home / ".claude.json", "acct-1")
                tick()
                login(home / ".claude.json", "acct-3")
                tick()
            self.assertEqual(popen.call_count, 5, "for ~/.claude the login without the variable counts too")

    def test_the_tick_starts_remind_once_a_minute_with_remind_on(self):
        stamp = self.tmp / "flowkit" / ".remind-tick"
        with mock.patch.object(self.tk.subprocess, "Popen") as popen:
            self.tk.tick()
            self.assertEqual(popen.call_count, 0, "no handoff.conf: off")
            (self.tmp / "handoff.conf").write_text("remind=off\n")
            self.tk.remind()
            self.assertEqual(popen.call_count, 0)
            (self.tmp / "handoff.conf").write_text("remind=off\nremind=on\n")
            self.tk.tick()
            self.assertEqual(popen.call_args[0][0][1:], [str(PLUGIN / "scripts" / "handoff.py"), "remind"])
            self.tk.remind()
            self.assertEqual(popen.call_count, 1, "at most once a minute")
            os.utime(stamp, (0, 0))
            self.tk.remind()
            self.assertEqual(popen.call_count, 2, "a minute on")


class Pass(Tick):
    """A pass keeps its status line and who ran it; the status bar prints that line, and runs a
    pass itself only where no timer does."""

    def bar(self):
        return self.printed(self.tk.bar)[0]

    def age(self, **seconds):
        kept = self.kept()
        self.tk.CACHE.write_text(json.dumps({**kept, **{k: kept[k] - s for k, s in seconds.items()}}))

    def test_a_line_younger_than_ten_seconds_comes_from_the_last_pass(self):
        self.line = "⚑ 1 needs you "
        self.assertEqual((self.bar(), self.marked), ("⚑ 1 needs you ", 1), "no pass yet: the bar runs one")
        self.line = "⚑ 2 need you "
        self.assertEqual((self.bar(), self.marked), ("⚑ 1 needs you ", 1))
        self.age(at=11)
        self.assertEqual((self.bar(), self.marked), ("⚑ 2 need you ", 2))
        self.assertNotIn("timer", self.kept(), "the status bar's pass is no timer's")

    def test_while_a_timer_keeps_the_tick_the_status_bar_only_prints(self):
        self.line = "⚑ 1 needs you "
        self.tk.tick(timer=True)
        self.line = "⚑ 2 need you "
        self.age(at=30)
        self.assertEqual((self.bar(), self.marked), ("⚑ 1 needs you ", 1), "older than ten seconds, and still only printed")
        self.age(at=31, timer=61)
        self.assertEqual((self.bar(), self.marked), ("⚑ 2 need you ", 2), "no timed pass for a minute: the timer went")
        timed = self.kept()["timer"]
        self.assertLess(timed, time.time() - 60, "a pass of the status bar keeps the timer's stamp as it was")
        self.tk.tick(timer=True)
        self.assertGreater(self.kept()["timer"], timed)

    def test_an_overlapping_pass_prints_the_kept_line_and_runs_nothing(self):
        self.line = "fresh"
        self.tk.CACHE.write_text(json.dumps({"at": 0, "line": "stale"}))
        with self.tk.CACHE.with_suffix(".lock").open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertIsNone(self.tk.tick(timer=True))
            self.assertEqual((self.bar(), self.marked), ("stale", 0))
        self.assertEqual((self.bar(), self.marked), ("fresh", 1))

    def test_a_job_that_fails_stops_no_other(self):
        ran = []

        def broken():
            raise FileNotFoundError(2, "No such file or directory", "claude")

        def later():
            ran.append("later")

        self.tk.JOBS = (broken, later)
        self.line = "⚑ 1 needs you "
        with mock.patch.object(self.tk.subprocess, "Popen", side_effect=FileNotFoundError(2, "No such file", "python3")):
            kept = self.tk.tick(timer=True)
        self.assertEqual((ran, self.marked, kept["line"]), (["later"], 1, "⚑ 1 needs you "))
        self.assertEqual(list(kept["failed"]), ["broken"])
        self.assertIn("claude", kept["failed"]["broken"])
        out, code = self.printed(self.tk.status)
        self.assertEqual(code, 0)
        self.assertRegex(out, r"^tick: last pass 0 s ago, kept by the timer\nline: ⚑ 1 needs you \n"
                              r"failed: broken: FileNotFoundError: .*claude")
        self.tk.JOBS = (later,)
        self.assertNotIn("failed", self.tk.tick(), "a clean pass forgets it")

    def test_a_job_whose_program_is_missing_starts_nothing_and_stops_nothing(self):
        (self.tmp / "handoff.conf").write_text("remind=on\n")
        (self.tmp / "capacity" / "queue").mkdir(parents=True)
        (self.tmp / "capacity" / "queue" / "1.json").write_text("{}")
        with mock.patch.object(self.tk.subprocess, "Popen", side_effect=FileNotFoundError(2, "No such file", "python3")) as popen:
            kept = self.tk.tick()
        self.assertEqual((popen.call_count, self.marked), (2, 1), "the drain and the reminders tried, the marks ran")
        self.assertNotIn("failed", kept)

    def test_the_marks_are_flowkit_tmux_s_and_a_failure_is_only_theirs(self):
        self.tk = load()  # the real marks job
        self.tk.CLAUDE = self.tmp
        self.tk.CONFIG = self.tmp
        self.tk.CACHE = self.tmp / "flowkit" / "tick.json"
        self.tk.JOBS = ()
        done = subprocess.CompletedProcess([], 0, "⚑ 1 needs you ", "")
        with mock.patch.object(self.tk.shutil, "which", return_value=None), \
                mock.patch.object(self.tk.subprocess, "run", return_value=done) as run:
            self.assertEqual((self.tk.tick()["line"], run.call_count), ("", 0), "no tmux on this computer: nothing is run")
        with mock.patch.object(self.tk.shutil, "which", return_value="/usr/bin/tmux"), \
                mock.patch.object(self.tk.subprocess, "run", return_value=done) as run:
            self.assertEqual(self.tk.tick()["line"], "⚑ 1 needs you ")
            self.assertEqual(run.call_args[0][0][1:], [str(BIN.parent / "flowkit-tmux"), "marks"])
            run.return_value = subprocess.CompletedProcess([], 1, "", "Traceback\nKeyError: 'pid'\n")
            kept = self.tk.tick()
            self.assertEqual((kept["line"], kept["failed"]), ("", {"marks": "RuntimeError: KeyError: 'pid'"}))
            run.side_effect = subprocess.TimeoutExpired("flowkit-tmux", 120)
            self.assertIn("TimeoutExpired", self.tk.tick()["failed"]["marks"])

    def test_what_an_older_release_left_in_the_temp_folder_does_no_harm(self):
        """Before 0.14.0 the cache and the lock were flowkit-tmux-<uid>.json and .lock in the temp
        folder: a stale line there is never printed, and a lock held there (an older copy still
        ticking) stops no pass."""
        with mock.patch.object(tempfile, "tempdir", str(self.tmp)):  # never this computer's own temp folder
            old = Path(tempfile.gettempdir()) / f"flowkit-tmux-{os.getuid()}.json"
            old.write_text(json.dumps({"at": time.time(), "line": "an old line"}))
            with old.with_suffix(".lock").open("w") as held:
                fcntl.flock(held, fcntl.LOCK_EX)
                self.line = "⚑ 1 needs you "
                self.assertEqual((self.bar(), self.marked), ("⚑ 1 needs you ", 1))
        self.assertEqual(json.loads(old.read_text())["line"], "an old line", "left as it was")
        self.assertEqual(sorted(p.name for p in (self.tmp / "flowkit").iterdir() if p.is_file()), ["tick.json", "tick.lock"])

    def test_status_says_who_keeps_the_tick(self):
        out, code = self.printed(self.tk.status)
        self.assertEqual((out, code), (f"tick: no pass yet ({self.tk.CACHE} is not there)\n", 1))
        self.tk.tick()
        out, code = self.printed(self.tk.status)
        self.assertEqual((out, code), ("tick: last pass 0 s ago, run from the tmux status bar or by hand: no timer kept it "
                                       "in the last minute\nline: nothing waits on you in tmux\n", 0))
        self.tk.tick(timer=True)
        self.age(at=200, timer=200)
        out, code = self.printed(self.tk.status)
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("tick: stopped, last pass 3 min ago, run from the tmux status bar or by hand"), out)

    def test_a_broken_cache_is_no_pass_yet(self):
        self.tk.CACHE.write_text("[]")
        self.assertEqual(self.bar(), "")
        self.tk.CACHE.write_text(json.dumps({"at": "soon", "timer": "x", "line": None}))
        self.assertEqual(self.bar(), "")
        self.assertEqual(self.printed(self.tk.status)[1], 1)


class Commands(unittest.TestCase):
    """The commands as a tmux.conf and the timer run them: the copies in a HOME of the test's own,
    with a `tmux` and a `claude` there that serve one pane with a session waiting in it."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.home, self.claude = self.tmp / "home", self.tmp / "claude"
        self.bin = self.home / ".local" / "bin"  # first on the PATH the commands give themselves
        self.bin.mkdir(parents=True)
        self.claude.mkdir()
        pane = [str(os.getpid()), "%1", "work", "@1", "1", "w", "1", "2.1.304", "", "", "", "", "", "", ""]
        (self.tmp / "panes").write_text(SEP.join(pane) + "\n")
        (self.tmp / "windows").write_text(SEP.join(["@1", "", ""]) + "\n")
        self.agents([{"kind": "interactive", "pid": os.getpid(), "sessionId": "s1", "status": "waiting", "name": "demo-s1"}])
        (self.bin / "tmux").write_text(f"""#!/bin/sh
case "$1" in
  list-panes) cat {self.tmp / 'panes'};;
  list-windows) cat {self.tmp / 'windows'};;
  *) echo "$@" >> {self.tmp / 'tmux.calls'};;
esac
""")
        (self.bin / "claude").write_text(f"#!/bin/sh\necho asked >> {self.tmp / 'claude.calls'}\ncat {self.tmp / 'agents.json'}\n")
        for name in ("tmux", "claude"):
            (self.bin / name).chmod(0o755)
        self.env = {"HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.claude), "XDG_CONFIG_HOME": str(self.tmp / "xdg"),
                    "PATH": os.environ["PATH"], "TMPDIR": str(self.tmp)}

    def agents(self, rows):
        (self.tmp / "agents.json").write_text(json.dumps(rows))

    def asked(self):
        calls = self.tmp / "claude.calls"
        return len(calls.read_text().splitlines()) if calls.exists() else 0

    def kit(self, *files):
        for name in files:
            shutil.copyfile(PLUGIN / "bin" / name, self.bin / name)
            (self.bin / name).chmod(0o755)

    def sh(self, line):
        return subprocess.run(["sh", "-c", line], capture_output=True, text=True, env=self.env, timeout=60)

    def kept(self):
        return json.loads((self.claude / "flowkit" / "tick.json").read_text())

    def age(self, **seconds):
        kept = self.kept()
        (self.claude / "flowkit" / "tick.json").write_text(json.dumps({**kept, **{k: kept[k] - s for k, s in seconds.items()}}))

    def test_a_tmux_conf_written_before_the_timer_still_gets_its_line_and_its_tick(self):
        """0.11.0 to 0.13.0 wrote `#($HOME/.local/bin/flowkit-tmux waiting)` into the status bar."""
        self.kit("flowkit-tick", "flowkit-tmux")
        out = self.sh("$HOME/.local/bin/flowkit-tmux waiting")
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "#[reverse] ⚑ 1 needs you #[noreverse] ", ""))
        kept = self.kept()
        self.assertLess(time.time() - kept["at"], 30)
        self.assertNotIn("timer", kept)
        self.assertNotIn("failed", kept)
        self.assertIn("@flowkit_state waiting", (self.tmp / "tmux.calls").read_text(), "the pass marked the pane")
        self.assertEqual(self.asked(), 1)
        self.sh("$HOME/.local/bin/flowkit-tmux waiting")
        self.assertEqual(self.asked(), 1, "a line younger than ten seconds is only printed")
        self.age(at=11)
        self.agents([])
        self.assertEqual((self.sh("$HOME/.local/bin/flowkit-tmux waiting").stdout, self.asked()), ("", 2), "the tick goes on")

    def test_the_status_bar_prints_what_the_timer_s_pass_kept(self):
        self.kit("flowkit-tick", "flowkit-tmux")
        out = self.sh("$HOME/.local/bin/flowkit-tick --timer")
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""), "the timer's pass prints nothing")
        self.assertEqual(self.asked(), 1)
        self.agents([])
        self.age(at=30)
        for command in ("flowkit-tick bar", "flowkit-tmux waiting"):  # the kit's conf, and one written before 0.14.0
            out = self.sh(f"$HOME/.local/bin/{command}")
            self.assertEqual((out.stdout, out.stderr, self.asked()), ("#[reverse] ⚑ 1 needs you #[noreverse] ", "", 1), command)
        out = self.sh("$HOME/.local/bin/flowkit-tick status")
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertRegex(out.stdout, r"^tick: last pass \d+ s ago, kept by the timer\nline: #\[reverse\] ⚑ 1 needs you")

    def test_a_pass_goes_on_where_claude_or_tmux_is_not_what_it_should_be(self):
        """`claude` gone from the PATH used to end the pass in a traceback, before any job ran."""
        self.kit("flowkit-tick", "flowkit-tmux")
        (self.bin / "claude").unlink()
        python = os.path.dirname(shutil.which("python3"))  # wherever this system keeps it
        self.env["PATH"] = f"{python}:/usr/bin:/bin"  # the commands add ~/.local/bin, /opt/homebrew/bin and /usr/local/bin
        if shutil.which("claude", path=f"/opt/homebrew/bin:/usr/local/bin:{python}:/usr/bin:/bin"):
            self.skipTest("a claude in one of those folders answers in its place")
        out = self.sh("$HOME/.local/bin/flowkit-tick; $HOME/.local/bin/flowkit-tick status")
        self.assertEqual(out.stderr, "")
        self.assertRegex(out.stdout, r"^tick: last pass \d+ s ago, run from the tmux status bar or by hand")
        self.assertNotIn("failed:", out.stdout, "the marks stay as they are, as when `claude agents` fails")

    def test_a_status_bar_whose_tick_is_not_copied_yet_prints_nothing(self):
        self.kit("flowkit-tmux")
        out = self.sh("$HOME/.local/bin/flowkit-tmux waiting")
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))

    def test_marks_prints_the_line_for_the_tick(self):
        out = subprocess.run([sys.executable, str(PLUGIN / "bin" / "flowkit-tmux"), "marks"], capture_output=True, text=True,
                             env=self.env)
        self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "#[reverse] ⚑ 1 needs you #[noreverse] ", ""))


if __name__ == "__main__":
    unittest.main()
