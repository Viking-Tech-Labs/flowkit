"""Run with `python3 -m unittest discover plugins/coordinate/tests`. A stub `claude` serves the
session list and records what it was called with, so nothing reaches a real session."""
import importlib.util
import json
import os
import subprocess
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "coordinate.py"


class Coordinate(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "bin").mkdir()
        self.calls = self.tmp / "calls"
        self.env = {**os.environ, "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}"}

    def agents(self, rows):
        (self.tmp / "agents.json").write_text(json.dumps(rows))
        stub = self.tmp / "bin" / "claude"
        stub.write_text(f"#!/bin/sh\nif [ \"$1\" = agents ]; then cat {self.tmp / 'agents.json'}; "
                        f"else echo \"$@\" >> {self.calls}; echo 'backgrounded · abc'; fi\n")
        stub.chmod(0o755)

    def run_(self, *args):
        return subprocess.run(["python3", str(SCRIPT), *args], capture_output=True, text=True,
                              env=self.env)

    def test_workers_states(self):
        self.agents([
            {"name": "s-a", "pid": 10, "kind": "background", "id": "a1", "sessionId": "A", "cwd": "/r"},
            {"name": "s-b", "kind": "background", "id": "b1", "sessionId": "B", "state": "done"},
            {"name": "s-c", "kind": "interactive", "sessionId": "C"},
        ])
        out = self.run_("workers", "s-a", "s-b", "s-c", "s-d").stdout.split("\n")
        self.assertEqual([line.split()[1] for line in out if line],
                         ["live", "retired", "ended", "missing"])

    def test_live_entry_wins_over_older(self):
        self.agents([
            {"name": "s-a", "kind": "background", "id": "old", "sessionId": "O", "startedAt": 2},
            {"name": "s-a", "pid": 5, "kind": "background", "id": "new", "sessionId": "N", "startedAt": 1},
        ])
        self.assertIn(" live new ", self.run_("workers", "s-a").stdout)

    def test_wake_resumes_retired_without_other_flags(self):
        self.agents([{"name": "s-b", "kind": "background", "id": "b1", "sessionId": "B",
                      "cwd": str(self.tmp)}])
        p = self.run_("wake", "s-b", "go on")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.calls.read_text().strip(), "--bg --resume B wake: go on")

    def test_wake_refuses_live(self):
        self.agents([{"name": "s-a", "pid": 10, "kind": "background", "sessionId": "A"}])
        p = self.run_("wake", "s-a", "hi")
        self.assertEqual(p.returncode, 5)
        self.assertFalse(self.calls.exists())

    def test_spawner_prefers_installed_handoff_over_path(self):
        old, new = self.tmp / "old" / "bin", self.tmp / "handoff" / "0.2.0" / "bin"
        for d in (old, new):
            d.mkdir(parents=True)
            (d / "flowkit-spawn").write_text("#!/bin/sh\n")
            (d / "flowkit-spawn").chmod(0o755)
        (self.tmp / "claude" / "plugins").mkdir(parents=True)
        (self.tmp / "claude" / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"plugins": {"handoff@flowkit": [{"installPath": str(new.parent)}]}}))
        self.env.update(CLAUDE_CONFIG_DIR=str(self.tmp / "claude"), CLAUDE_CODE_PLUGIN_DIRS="",
                        PATH=f"{old}:{self.env['PATH']}")
        self.assertEqual(self.run_("spawner").stdout.strip(), str(new / "flowkit-spawn"))

    def test_self_not_listed(self):
        self.agents([])
        self.assertEqual(self.run_("self").returncode, 4)

    def test_self_growth_from_the_transcript(self):
        sid = "c0ffee00-0000-0000-0000-000000000000"
        self.agents([{"name": "epic-coord", "pid": os.getpid(), "id": "c1", "sessionId": sid}])
        project = self.tmp / "claude" / "projects" / "-r"
        project.mkdir(parents=True)
        now = time.time()
        stamp = lambda hours: datetime.fromtimestamp(now - hours * 3600, timezone.utc).isoformat()
        turn = lambda hours, k, **kw: {"type": "assistant", "timestamp": stamp(hours), **kw, "message": {"usage": {
            "input_tokens": 2, "cache_read_input_tokens": k * 1000 - 3, "output_tokens": 1}}}
        compact = lambda hours, pre, post=None: {"type": "system", "subtype": "compact_boundary", "timestamp": stamp(hours),
                                                 "compactMetadata": {"preTokens": pre * 1000, **(
                                                     {"postTokens": post * 1000} if post else {})}}
        lines = [turn(30, 50),                   # the size when the 24 h began
                 turn(10, 150), compact(9, 160, 30),  # dropped 130k
                 turn(5, 90), compact(4, 95),    # no postTokens: the next turn's size stands in
                 turn(3, 40), turn(1, 80),       # dropped 55k; 80k now
                 turn(1, 999, isSidechain=True), {"type": "user", "timestamp": "not a time"}]
        (project / f"{sid}.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
        self.env.update(CLAUDE_CONFIG_DIR=str(self.tmp / "claude"), XDG_CONFIG_HOME=str(self.tmp / "xdg"))
        out = self.run_("self", "--growth")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.splitlines()[-1], "growth: 215k in 24 h (2 compactions, now 80k) · under 300k")
        (self.tmp / "xdg" / "flowkit").mkdir(parents=True)
        (self.tmp / "xdg" / "flowkit" / "capacity.conf").write_text("slots = 3\ncoordinator_growth = 200k\n")
        self.assertTrue(self.run_("self", "--growth").stdout.endswith("· over 200k\n"))
        spec = importlib.util.spec_from_file_location("coordinate", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.assertEqual(mod.growth(project / f"{sid}.jsonl", now - 2 * 3600), (175_000, 2, 40_000),
                         "a day in the past (capacity report) leaves out what came after it")
        (project / f"{sid}.jsonl").unlink()
        self.assertEqual(self.run_("self", "--growth").returncode, 8)


if __name__ == "__main__":
    unittest.main()
