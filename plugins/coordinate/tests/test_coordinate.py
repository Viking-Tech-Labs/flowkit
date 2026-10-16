"""Run with `python3 -m unittest discover plugins/coordinate/tests`. A stub `claude` serves the
session list and records what it was called with, so nothing reaches a real session."""
import json
import os
import subprocess
import tempfile
import unittest
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
        self.assertEqual(self.calls.read_text().strip(), "--bg --resume B go on")

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


if __name__ == "__main__":
    unittest.main()
