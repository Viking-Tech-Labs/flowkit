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

    def window(self, wid, story="", label=""):
        self.windows[wid] = label
        self.story = story
        self.wid = wid

    def session(self, n, status, live=None, idle_for=0, closed=False, retry=False, touched=None, name=""):
        """A claude pane in the current window, its agents row and, optionally, its sessions file."""
        sid = f"{n:08d}-0000-0000-0000-000000000000"
        pid = str(1000 + n)
        self.pane_list.append([pid, f"%{n}", "work", self.wid, "1", "w", str(n), "2.1.304", "", self.story])
        self.rows.append({"kind": "interactive", "pid": int(pid), "sessionId": sid, "status": status, "name": name})
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
        self.pane_list[-1][-1] = "st"
        self.assertEqual(self.label("@1"), "unchanged")
        self.pane_list[-1][-1] = "adhoc"
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


if __name__ == "__main__":
    unittest.main()
