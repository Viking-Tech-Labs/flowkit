"""Run with `python3 -m unittest discover plugins/handoff/tests`. Loads flowkit-tmux as a module and
swaps its command runner for a stub that serves panes, windows and `claude agents`, and records the
tmux writes, so nothing reaches a real tmux server or the user's files."""
import contextlib
import importlib.machinery
import io
import importlib.util
import json
import os
import tempfile
import time
import unittest
from pathlib import Path

BIN = Path(__file__).resolve().parent.parent / "bin" / "flowkit-tmux"
SEP = "\x1f"


def load():
    loader = importlib.machinery.SourceFileLoader("flowkit_tmux", str(BIN))
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
        self.ft.run = self.run_stub
        self.rows, self.pane_list, self.windows, self.writes = [], [], {}, []

    def run_stub(self, *cmd):
        if cmd[:2] == ("claude", "agents"):
            return json.dumps(self.rows)
        if cmd[0] == "ps":
            return ""
        if cmd[:2] == ("tmux", "list-panes"):
            return "".join(SEP.join(p) + "\n" for p in self.pane_list)
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

    def test_a_label_goes_when_its_sessions_do(self):
        self.window("@1", "st", label="running")
        self.assertIsNone(self.label("@1"))


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


if __name__ == "__main__":
    unittest.main()
