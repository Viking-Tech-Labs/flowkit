"""Run with `python3 -m unittest discover plugins/handoff/tests`. Loads flowkit-tmux as a module and
swaps its command runner for a stub that serves panes, windows and `claude agents`, and records the
tmux writes, so nothing reaches a real tmux server or the user's files."""
import contextlib
import fcntl
import importlib.machinery
import io
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

BIN = Path(__file__).resolve().parent.parent / "bin" / "flowkit-tmux"
SEP = "\x1f"


def load(path=BIN):
    loader = importlib.machinery.SourceFileLoader("flowkit_tmux", str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


class Labels(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for sub in ("stories", "sessions", "flowkit/retry", "projects/-repo"):
            (self.tmp / sub).mkdir(parents=True)
        self.ft = load()
        self.ft.CLAUDE = self.tmp
        self.ft.CACHE = self.tmp / "status.json"
        self.ft.run = self.run_stub
        self.rows, self.pane_list, self.windows, self.writes = [], [], {}, []
        self.later, self.listed, self.asked = None, 0, 0  # a second pane list, served from the second list-panes on

    def run_stub(self, *cmd):
        if cmd[:2] == ("claude", "agents"):
            self.asked += 1
            return json.dumps(self.rows)
        if cmd[0] == "ps":
            return ""
        if cmd[:2] == ("tmux", "list-panes"):
            self.listed += 1
            panes = self.later if self.later is not None and self.listed > 1 else self.pane_list
            return "".join(SEP.join(p) + "\n" for p in panes)
        if cmd[:2] == ("tmux", "list-windows"):
            return "".join(SEP.join([wid, "", label]) + "\n" for wid, label in self.windows.items())
        self.writes.append(cmd[1:])
        return ""

    def window(self, wid, story="", label="", hold=""):
        self.windows[wid] = label
        self.story = story
        self.wid = wid
        self.hold = hold

    def pane(self, pid, n, cmd, born=""):
        slug = "" if self.story == "adhoc" else self.story
        self.pane_list.append([pid, f"%{n}", "work", self.wid, "1", "w", str(n), cmd, "", self.story, slug, self.hold, "",
                               born])

    def session(self, n, status, live=None, idle_for=0, closed=False, retry=False, touched=None, name=""):
        """A claude pane in the current window, its agents row and, optionally, its sessions file."""
        sid = f"{n:08d}-0000-0000-0000-000000000000"
        pid = str(1000 + n)
        self.pane(pid, n, "2.1.304")
        self.rows.append({"kind": "interactive", "pid": int(pid), "sessionId": sid, "status": status, "name": name,
                          "startedAt": (time.time() - 2 * 3600) * 1000})
        updated = (time.time() - idle_for) * 1000
        (self.tmp / "sessions" / f"{pid}.json").write_text(json.dumps(
            {"pid": int(pid), "sessionId": sid, "status": live or status, "statusUpdatedAt": updated}))
        transcript = self.tmp / "projects" / "-repo" / f"{sid}.jsonl"
        transcript.write_text("{}\n")
        old = time.time() - (idle_for if touched is None else touched)
        os.utime(transcript, (old, old))
        if closed:
            with (self.tmp / "stories" / f"{self.story or 'other'}.md").open("a") as f:
                f.write(f"\n## s{n} · 2026-10-20 12:00\nSession: {sid} · Repo: /repo\n")
        if retry:
            (self.tmp / "flowkit" / "retry" / f"{sid}.json").write_text("{}")
        return sid

    def arm(self, name, story="st"):
        """An open watch of the story: its arm file, as scripts/watch.py keeps it until its section is in."""
        path = self.tmp / "flowkit" / "watches" / f"{name}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"name": name, "story": story, "state": "pending"}))
        return path

    def label(self, wid):
        self.writes.clear()
        self.ft.closed.cache_clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.ft.waiting()
        for args in self.writes:
            for i, arg in enumerate(args):
                if arg == "@flowkit_tab" and args[i - 1] == wid:
                    return None if args[i - 3] == "-u" else args[i + 1]
        return "unchanged"

    def one(self, **kw):
        self.setUp()
        self.window("@1", "st")
        self.session(1, **kw)
        return self.label("@1")

    def test_each_state(self):
        self.assertEqual(self.one(status="waiting"), "needs you")
        self.assertEqual(self.one(status="busy", retry=True), "error")
        self.assertEqual(self.one(status="busy"), "running")
        self.assertEqual(self.one(status="busy", live="shell"), "running")
        self.assertEqual(self.one(status="busy", live="shell", closed=True), "watching")
        self.assertEqual(self.one(status="idle", idle_for=16 * 60), "stalled")
        self.assertEqual(self.one(status="idle", closed=True), "done")
        self.assertEqual(self.one(status="idle", idle_for=60), "unchanged")
        self.assertEqual(self.one(status="idle", idle_for=16 * 60, name="st-report"), "unchanged")

    def test_not_stalled_while_the_transcript_or_a_fork_moves(self):
        self.assertEqual(self.one(status="idle", idle_for=16 * 60, touched=60), "unchanged")
        self.setUp()
        self.window("@1", "st")
        sid = self.session(1, status="idle", idle_for=16 * 60)
        forks = self.tmp / "projects" / "-repo" / sid / "subagents"
        forks.mkdir(parents=True)
        (forks / "agent-a.jsonl").write_text("{}\n")
        self.assertEqual(self.label("@1"), "unchanged")

    def test_a_reused_pid_is_not_this_session(self):
        self.setUp()
        self.window("@1", "st")
        self.session(1, status="busy", live="shell", closed=True)
        path = self.tmp / "sessions" / "1001.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "sessionId": "someone-else"}))
        self.assertEqual(self.label("@1"), "running")

    def test_a_close_in_another_story_counts_only_on_the_adhoc_tab(self):
        self.window("@1", "st")
        self.story = "other"
        self.session(1, status="idle", closed=True)
        self.story = "st"
        self.pane_list[-1][9] = "st"
        self.assertEqual(self.label("@1"), "unchanged")
        self.pane_list[-1][9] = "adhoc"
        self.assertEqual(self.label("@1"), "done")

    def test_most_urgent_wins_and_workspace_windows_stay_bare(self):
        self.window("@1", "st", label="done")
        self.session(1, status="idle", closed=True)
        self.session(2, status="busy", live="shell", closed=True)
        self.session(3, status="busy")
        self.session(4, status="idle", idle_for=20 * 60)
        self.assertEqual(self.label("@1"), "stalled")
        self.session(5, status="busy", retry=True)
        self.assertEqual(self.label("@1"), "error")
        self.session(6, status="waiting")
        self.assertEqual(self.label("@1"), "needs you")
        self.window("@2")
        self.session(7, status="waiting")
        self.assertEqual(self.label("@2"), "unchanged")

    def test_a_story_with_an_open_watch_is_watching_while_nothing_is_more_urgent(self):
        self.window("@1", "st", label="done")
        self.session(1, status="idle", idle_for=60, name="st-report")
        self.session(2, status="idle", closed=True)
        self.window("@2", "other")
        self.session(3, status="idle", closed=True)
        self.assertEqual((self.label("@1"), self.label("@2")), ("unchanged", "done"))
        arm = self.arm("app!7")
        (arm.parent / ".app!7.json.123").write_text("{")  # a write caught halfway
        self.assertEqual((self.label("@1"), self.label("@2")), ("watching", "done"))
        self.session(4, status="busy")
        self.assertEqual(self.label("@2"), "running")
        self.wid, self.story = "@1", "st"
        self.session(5, status="idle", idle_for=20 * 60)
        self.assertEqual(self.label("@1"), "stalled")
        self.rows.pop(), self.pane_list.pop()
        arm.unlink()
        self.assertEqual(self.label("@1"), "unchanged")  # back to done, which the tab still says

    def test_a_label_goes_when_its_sessions_do(self):
        self.window("@1", "st", label="running")
        self.assertIsNone(self.label("@1"))


class Cache(Labels):
    """The status-bar job answers from its cache for 10 seconds, then asks `claude agents` again;
    a run that overlaps another prints the cache; `list` always asks."""

    def setUp(self):
        super().setUp()
        self.window("@1", "st")

    def line(self):
        self.writes.clear()
        self.ft.closed.cache_clear()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.ft.status_line()
        return out.getvalue()

    def test_the_tick_drains_the_capacity_queue(self):
        queue = self.tmp / "capacity" / "queue"
        with mock.patch.object(self.ft.subprocess, "Popen") as popen:
            self.line()
            queue.mkdir(parents=True)
            (queue / ".seq").write_text("1\n")
            self.ft.CACHE.unlink()
            self.line()
            self.assertEqual(popen.call_count, 0, "an empty queue costs one directory read")
            (queue / "1.json").write_text("{}")
            self.ft.CACHE.unlink()
            self.line()
            self.assertEqual(popen.call_count, 1)
            self.assertEqual(popen.call_args[0][0][1:], [str(BIN.parent.parent / "scripts" / "capacity.py"), "drain"])
            self.ft.CACHE.unlink()
            self.line()
            self.assertEqual(popen.call_count, 1, "at most every FLOWKIT_CAPACITY_POLL seconds")
            with mock.patch.dict(os.environ, {"FLOWKIT_CAPACITY_POLL": "0"}):
                self.ft.CACHE.unlink()
                self.line()
            self.assertEqual(popen.call_count, 2)

    def test_the_installed_copy_drains_through_the_installed_plugin(self):
        """The kit's copy in ~/.local/bin is no plugin (the resolver the kit puts in ~/.local/scripts
        notwithstanding): the drain starts the capacity.py of the newest handoff in
        installed_plugins.json that has one."""
        copy = self.tmp / "home" / ".local" / "bin" / "flowkit-tmux"
        copy.parent.mkdir(parents=True)
        shutil.copyfile(BIN, copy)
        (copy.parent.parent / "scripts").mkdir()
        (copy.parent.parent / "scripts" / "capacity.py").write_text("# the kit's resolver for a 0.8.0 tick\n")
        self.ft = load(copy)
        self.ft.CLAUDE = self.tmp
        (self.tmp / "plugins").mkdir()
        (self.tmp / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {"handoff@flowkit": [
            {"installPath": str(self.tmp / "gone")}, {"installPath": str(BIN.parent.parent)}]}}))
        queue = self.tmp / "capacity" / "queue"
        queue.mkdir(parents=True)
        (queue / "1.json").write_text("{}")
        with mock.patch.object(self.ft.subprocess, "Popen") as popen:
            self.ft.drain()
        self.assertEqual(popen.call_args[0][0][1:], [str(BIN.parent.parent / "scripts" / "capacity.py"), "drain"])
        (self.tmp / "flowkit" / "watches").mkdir()
        (self.tmp / "flowkit" / "watches" / "repo!12.json").write_text("{}")
        with mock.patch.object(self.ft.subprocess, "Popen") as popen:
            self.ft.watch()
        self.assertEqual(popen.call_args[0][0][1:], [str(BIN.parent.parent / "scripts" / "watch.py"), "run"])

    def test_the_tick_starts_a_watch_poller_that_went(self):
        state = self.tmp / "flowkit" / "watches"
        state.mkdir()
        gone = subprocess.Popen(["true"])
        gone.wait()
        with mock.patch.object(self.ft.subprocess, "Popen") as popen:
            self.line()
            self.assertEqual(popen.call_count, 0, "no watch is open: one directory read")
            (state / "repo!12.json").write_text("{}")
            (state / "poller.pid").write_text(str(os.getpid()))
            self.ft.watch()
            self.assertEqual(popen.call_count, 0, "its poller runs and beats")
            os.utime(state / "poller.pid", (0, 0))
            self.ft.CACHE.unlink()
            self.line()
            self.assertEqual(popen.call_args[0][0][1:], [str(BIN.parent.parent / "scripts" / "watch.py"), "run"],
                             "a heartbeat older than three minutes")
            self.ft.watch()
            self.assertEqual(popen.call_count, 1, "at most once a minute")
            (state / "poller.pid").write_text(str(gone.pid))
            os.utime(state / ".tick", (0, 0))
            self.ft.watch()
            self.assertEqual(popen.call_count, 2, "a fresh heartbeat of a process that is gone")

    def test_a_line_younger_than_ten_seconds_comes_from_the_cache(self):
        self.session(1, status="waiting")
        first = self.line()
        self.assertIn("1 needs you", first)
        self.assertEqual((self.asked, self.line(), self.asked), (1, first, 1))
        stale = json.loads(self.ft.CACHE.read_text())
        stale["at"] -= 11
        self.ft.CACHE.write_text(json.dumps(stale))
        self.assertEqual((self.line(), self.asked), (first, 2))

    def test_an_overlapping_run_prints_the_cache_and_asks_nothing(self):
        self.session(1, status="waiting")
        self.ft.CACHE.write_text(json.dumps({"at": 0, "line": "stale"}))
        with self.ft.CACHE.with_suffix(".lock").open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual((self.line(), self.asked), ("stale", 0))
        self.assertIn("1 needs you", self.line())
        self.assertEqual(self.asked, 1)

    def test_list_asks_every_time(self):
        self.session(1, status="waiting")
        self.line()
        self.ft.input = lambda prompt="": ""
        with contextlib.redirect_stdout(io.StringIO()):
            self.ft.list_sessions()
            self.ft.list_sessions()
        self.assertEqual(self.asked, 3)


class Closer(Labels):
    """autoclose=on: a finished story's tab goes as a whole, reporter last; anything else stays."""

    def setUp(self):
        super().setUp()
        self.ft.CONFIG = self.tmp
        (self.tmp / "handoff.conf").write_text("driver=tmux\nautoclose=on\n")
        self.killed = []
        self.ft.terminate = lambda pid: self.killed.append(pid) or True
        self.window("@1", "st")

    def note(self, *lines, age=10 * 60):
        path = self.tmp / "stories" / "st.md"
        with path.open("a") as f:
            f.write("".join(f"{line}\n" for line in lines))
        os.utime(path, (time.time() - age, time.time() - age))

    def shell(self, n, born=""):
        self.pane(str(5000 + n), n, "zsh", born)

    def closes(self):
        self.writes.clear()
        self.listed = 0
        self.ft.closed.cache_clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.ft.waiting()
        return [a[2] for a in self.writes if a[0] == "kill-pane"]

    def rang(self):
        return any("report now" in a for a in self.writes)

    def finished(self):
        """A story of two sessions, both closed, with its reporter's final report in."""
        self.note("# st", "Next: spawned s2 (opus/high)")
        self.session(2, status="idle", idle_for=10 * 60, closed=True)
        self.session(1, status="idle", idle_for=10 * 60, name="st-report")
        self.session(3, status="idle", idle_for=10 * 60, closed=True)
        self.note(self.final())

    def final(self):
        return f"## Final report · {time.strftime('%Y-%m-%d %H:%M', time.localtime(time.time() - 3600))}"

    def test_a_finished_story_goes_reporter_last(self):
        self.finished()
        self.shell(4)
        self.assertEqual(self.closes(), ["%4", "%2", "%3", "%1"])
        self.assertEqual(self.killed, [1002, 1003, 1001])

    def test_a_final_report_naming_missing_sessions_ends_the_wait(self):
        self.note("# st", "Next: spawned s2, s9")
        self.session(2, status="idle", idle_for=20 * 60, closed=True)
        self.session(1, status="idle", idle_for=20 * 60, name="st-report")
        self.session(3, status="idle", idle_for=20 * 60)  # never closed
        self.assertEqual(self.closes(), [])
        self.assertTrue(self.rang())
        self.note(self.final(), "s9 never closed; s3 recorded from its transcript")
        self.assertEqual(self.closes(), ["%2", "%3", "%1"])

    def test_a_session_started_after_the_final_report_needs_its_own_close(self):
        self.finished()
        self.session(5, status="idle", idle_for=10 * 60)
        self.rows[-1]["startedAt"] = (time.time() - 30 * 60) * 1000
        self.assertEqual(self.closes(), [])

    def test_a_complete_story_rings_its_reporter_until_the_report_is_in(self):
        self.note("# st", "Next: spawned s2")
        self.session(2, status="idle", idle_for=10 * 60, closed=True)
        self.session(1, status="idle", idle_for=10 * 60, name="st-report")
        self.assertEqual(self.closes(), [])
        self.assertTrue(self.rang())
        self.pane_list[-1][12] = str(int(time.time()))
        self.closes()
        self.assertFalse(self.rang())  # rung a minute ago: not again yet

    def test_an_open_watch_keeps_the_story_and_its_reporter_unrung(self):
        self.note("# st")
        sid = self.session(2, status="idle", idle_for=20 * 60)
        self.note("## s2 · 2026-11-17 15:20", f"Session: {sid} · Repo: /repo",
                  "Next: watching app!7 (https://gitlab.com/g/p/-/merge_requests/7)")
        self.session(1, status="idle", idle_for=20 * 60, name="st-report")
        arm = self.arm("app!7")
        self.assertEqual(self.closes(), ["%2"], "the slice's pane goes; the story and its reporter stay")
        self.assertFalse(self.rang(), "a watch longer than the backfill's 15 minutes")
        self.rows.pop(0), self.pane_list.pop(0)
        self.assertEqual(self.closes(), [])
        self.assertFalse(self.rang())
        arm.unlink()  # gone with no section: the promise is still open, and the backfill is back
        self.assertEqual(self.closes(), [])
        self.assertTrue(self.rang())
        self.note("## app!7 · 2026-11-17 16:05", "Watch: pipeline green 16:04", "Next: none")
        self.pane_list[-1][12] = ""
        self.assertEqual(self.closes(), [])
        self.assertTrue(self.rang(), "complete: the reporter writes its final report")
        self.note(self.final())
        self.assertEqual(self.closes(), ["%1"])

    def test_a_tab_whose_claudes_exited_still_closes(self):
        self.note("# st", "Next: spawned s2")
        self.shell(1)
        self.shell(2)
        self.assertEqual(self.closes(), ["%1", "%2"])
        self.assertEqual(self.killed, [])

    def test_a_tab_flowkit_spawn_just_opened_stays_until_its_sessions_come_up(self):
        self.note("# st", "Next: spawned s2")
        self.shell(1, born=str(int(time.time())))
        self.shell(2, born=str(int(time.time()) - 5))
        self.assertEqual(self.closes(), [])
        for p in self.pane_list:
            p[13] = str(int(time.time() - self.ft.SPAWN_GRACE - 1))
        self.assertEqual(self.closes(), ["%1", "%2"])

    def test_a_shell_just_reused_in_a_finished_tab_keeps_the_story(self):
        self.finished()
        self.shell(4, born=str(int(time.time())))
        self.assertEqual(self.closes(), [])
        self.assertEqual(self.killed, [])

    def test_a_launch_that_landed_since_the_pane_list_keeps_the_story(self):
        self.finished()
        self.shell(4)
        self.later = [list(p) for p in self.pane_list]
        self.later[-1][13] = str(int(time.time()))  # stamped between the list and the kill
        self.assertEqual(self.closes(), [])
        self.later[-1][13] = ""
        self.later[-1][7] = "2.1.304"  # its claude is up, not yet in claude agents
        self.assertEqual(self.closes(), [])
        self.assertEqual(self.killed, [])
        self.later = None
        self.assertEqual(self.closes(), ["%4", "%2", "%3", "%1"])

    def test_a_session_whose_transcript_or_fork_moved_lately_is_not_killed(self):
        self.finished()
        transcript = next((self.tmp / "projects" / "-repo").glob("00000003-*.jsonl"))
        os.utime(transcript, None)
        self.assertEqual(self.closes(), [])
        os.utime(transcript, (time.time() - 600, time.time() - 600))
        forks = transcript.with_suffix("") / "subagents"
        forks.mkdir(parents=True)
        (forks / "agent-a.jsonl").write_text("{}\n")
        self.assertEqual(self.closes(), [])

    def test_a_fresh_story_file_waits_out_the_grace(self):
        self.finished()
        self.note(age=60)
        self.writes.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.ft.waiting()
        self.assertEqual([a for a in self.writes if a[0] == "kill-pane"], [])

    def test_watching_working_and_held_tabs_stay(self):
        self.finished()
        self.session(5, status="busy", live="shell", closed=True)
        self.assertEqual(self.closes(), [])
        for hold in ("@flowkit_hold", "@cc_hold"):
            self.setUp()
            self.window("@1", "st", hold="1")
            self.finished()
            self.assertEqual(self.closes(), [], hold)

    def test_a_pane_running_something_else_keeps_the_story(self):
        self.finished()
        self.pane("6000", 6, "vim")
        self.assertEqual(self.closes(), [])

    def test_workspace_and_adhoc_windows_are_never_touched(self):
        self.window("@2")
        self.shell(1)
        self.session(2, status="idle", idle_for=60 * 60)
        self.window("@3", "adhoc")
        self.session(3, status="idle", idle_for=60 * 60, closed=True)
        self.assertEqual(self.closes(), [])
        self.assertEqual(self.killed, [])

    def test_autoclose_off_kills_nothing(self):
        for conf in ("driver=tmux\n", "autoclose=on\nautoclose=off\n"):
            (self.tmp / "handoff.conf").write_text(conf)
            self.finished()
            self.shell(4)
            self.assertEqual(self.closes(), [], conf)
            self.assertFalse(self.rang())
            self.setUp()
            (self.tmp / "handoff.conf").write_text(conf)


class PaneCloser(Closer):
    """autoclose=on: a finished slice's pane goes once its close is recorded and its successor runs."""

    def slice_done(self, n=1, nxt="s2", name="", **kw):
        """Session n idle 10 minutes with its close recorded, `Next: started <nxt>`."""
        sid = self.session(n, status=kw.pop("status", "idle"), idle_for=10 * 60, name=name or f"s{n}", **kw)
        self.note(f"## s{n} · 2026-11-13 09:00", f"Session: {sid} · Repo: /repo", f"Next: started {nxt}")
        return sid

    def successor(self, n=2, status="busy"):
        self.session(n, status=status, name=f"s{n}")

    def test_a_finished_slice_goes_once_its_successor_runs(self):
        self.slice_done()
        self.successor()
        self.assertEqual(self.closes(), ["%1"])
        self.assertEqual(self.killed, [1001])

    def test_a_successor_in_the_background_counts(self):
        self.slice_done()
        self.rows.append({"kind": "background", "pid": 4242, "name": "s2", "status": "busy"})
        self.assertEqual(self.closes(), ["%1"])

    def test_a_slice_that_registered_a_watch_goes_while_the_watch_is_open(self):
        sid = self.session(1, status="idle", idle_for=10 * 60, name="s1")
        self.note("## s1 · 2026-11-17 15:20", f"Session: {sid} · Repo: /repo",
                  "Next: watching app!7 (https://gitlab.com/g/p/-/merge_requests/7)")
        self.session(9, status="idle", idle_for=60, name="st-report")
        self.assertEqual(self.closes(), [], "no arm is open")
        self.arm("app!7", story="other")
        self.assertEqual(self.closes(), [], "another story's arm")
        self.arm("app!7")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.ft.closable()
        self.assertEqual(out.getvalue(), "would close work:w.1 %1 s1 (watch app!7 is open)\n")
        self.assertEqual(self.closes(), ["%1"])
        self.assertEqual(self.killed, [1001])

    def test_no_successor_running_or_no_close_recorded_keeps_the_pane(self):
        self.slice_done()
        self.assertEqual(self.closes(), [], "the successor is not running")
        self.setUp()
        self.session(1, status="idle", idle_for=10 * 60, name="s1")
        self.note("# st", "Next: started s2")  # a line outside its close section
        self.successor()
        self.assertEqual(self.closes(), [], "no close recorded")

    def test_the_reporter_and_a_coordinator_stay(self):
        self.slice_done(name="st-report")
        self.successor()
        self.assertEqual(self.closes(), [], "reporter")
        for name in ("st-coord", "st-coordinator", "coord-7"):
            self.setUp()
            self.slice_done(name=name)
            self.successor()
            self.assertEqual(self.closes(), [], name)
        self.setUp()
        (self.tmp / "coordination").mkdir()
        (self.tmp / "coordination" / "st.md").write_text("# st - coordination board\nCoordinator: `lead` · Mission: x\n")
        self.slice_done(name="lead")
        self.successor()
        self.assertEqual(self.closes(), [], "named on a coordination board")

    def test_a_busy_waiting_or_lately_touched_session_stays(self):
        for kw in ({"status": "busy"}, {"status": "waiting"}, {"touched": 60}, {"retry": True}):
            self.setUp()
            self.slice_done(**kw)
            self.successor()
            self.assertEqual(self.closes(), [], kw)
            self.assertEqual(self.killed, [], kw)

    def test_adhoc_unmarked_and_held_tabs_stay(self):
        for story, hold in (("adhoc", ""), ("", ""), ("st", "1")):
            self.setUp()
            self.window("@2", story, hold=hold)
            self.slice_done()
            self.successor()
            self.assertEqual(self.closes(), [], (story, hold))

    def test_autoclose_off_closes_no_pane(self):
        (self.tmp / "handoff.conf").write_text("driver=tmux\n")
        self.slice_done()
        self.successor()
        self.assertEqual(self.closes(), [])

    def test_closable_prints_and_closes_nothing(self):
        self.slice_done()
        self.successor()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.ft.closable()
        self.assertEqual(out.getvalue(), "would close work:w.1 %1 s1 (successor s2 runs)\n")
        self.assertEqual((self.killed, [a for a in self.writes if a[0] == "kill-pane"]), ([], []))


if __name__ == "__main__":
    unittest.main()
