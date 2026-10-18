"""Run with `python3 -m unittest discover plugins/handoff/tests`. Each test gets its own config
dir and a stub `claude` that serves a session list and records what it was called with, and a
stub `tmux` that serves a pane list, so nothing reaches a real session or the user's files."""
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
HANDOFF = PLUGIN / "scripts" / "handoff.py"
AWAY_BIN = PLUGIN / "bin" / "flowkit-away"


class Handoff(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "bin").mkdir()
        (self.tmp / "claude").mkdir()
        self.calls = self.tmp / "calls"
        self.flag = self.tmp / "claude" / "away"
        self.env = {**os.environ, "CLAUDE_CONFIG_DIR": str(self.tmp / "claude"),
                    "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}"}
        self.agents([])
        self.panes("")

    def agents(self, rows):
        (self.tmp / "agents.json").write_text(json.dumps(rows))
        stub = self.tmp / "bin" / "claude"
        stub.write_text(f"#!/bin/sh\nif [ \"$1\" = agents ]; then cat {self.tmp / 'agents.json'}; "
                        f"else echo \"$@\" >> {self.calls}; fi\n")
        stub.chmod(0o755)

    def panes(self, text):
        stub = self.tmp / "bin" / "tmux"
        stub.write_text(f"#!/bin/sh\nif [ \"$1\" = list-panes ]; then printf '%s\\n' '{text}'; "
                        f"else echo tmux \"$@\" >> {self.calls}; fi\n")
        stub.chmod(0o755)

    def handoff(self, *args, stdin=""):
        return subprocess.run(["python3", str(HANDOFF), *args], input=stdin, capture_output=True,
                              text=True, env=self.env)

    def away(self, *args):
        return subprocess.run(["python3", str(AWAY_BIN), *args], capture_output=True, text=True, env=self.env)

    def gate(self):
        out = self.handoff("question-gate", stdin=json.dumps({"tool_name": "AskUserQuestion"})).stdout
        return json.loads(out)["hookSpecificOutput"] if out else None

    def test_question_gate_follows_the_flag(self):
        self.assertIsNone(self.gate())
        self.flag.write_text("2026-10-18 23:10\n")
        out = self.gate()
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("since 2026-10-18 23:10", out["permissionDecisionReason"])
        self.assertIn('"(auto)"', out["permissionDecisionReason"])
        old = time.time() - 13 * 3600
        os.utime(self.flag, (old, old))
        self.assertIsNone(self.gate())

    def test_nudge_sorts_the_waiting_sessions(self):
        me, shell = os.getpid(), os.getppid()
        self.agents([
            {"name": "s1", "kind": "interactive", "pid": me, "status": "waiting", "waitingFor": "input needed"},
            {"name": "s2", "kind": "interactive", "pid": 1, "status": "waiting", "waitingFor": "input needed"},
            {"name": "s3", "kind": "interactive", "pid": 1, "status": "waiting", "waitingFor": "permission prompt"},
            {"name": "x-report", "kind": "interactive", "pid": 1, "status": "waiting", "waitingFor": "input needed"},
            {"name": "s4", "kind": "background", "id": "b4", "sessionId": "B4", "pid": 1, "status": "waiting",
             "waitingFor": "input needed"},
            {"name": "s5", "kind": "interactive", "pid": 1, "status": "waiting", "waitingFor": "dialog open"},
            {"name": "s6", "kind": "interactive", "pid": 1, "status": "idle"},
        ])
        self.panes(f"{shell} %7")
        out = self.away("nudge", "--dry-run")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.splitlines(), [
            "would nudge s1 (tmux %7)",
            "unreachable s2: waits on a question outside tmux; answer it from the phone",
            "permission s3: waits on a permission prompt; answer it from the phone",
            "skipped x-report: a reporter never asks",
            "would nudge s4 (background)",
            "waiting s5: waits on dialog open",
        ])
        self.assertFalse(self.calls.exists())
        only = self.away("nudge", "--dry-run", "--only", "s6", "--only", "s3").stdout.splitlines()
        self.assertEqual(only, ["permission s3: waits on a permission prompt; answer it from the phone",
                                "s6: not waiting (idle)"])

    def test_nothing_waits(self):
        self.agents([{"name": "s1", "kind": "interactive", "pid": 1, "status": "busy"}])
        self.assertEqual(self.away("nudge").stdout.strip(), "no session waits on you")
        self.assertEqual(self.away("waiting").stdout.strip(), "nobody waits on you")
        self.assertEqual(self.away("nudge", "--what").returncode, 2)

    def test_away_writes_the_flag_then_nudges_and_back_lists(self):
        self.agents([
            {"name": "s3", "kind": "interactive", "pid": 1, "status": "waiting", "waitingFor": "permission prompt"},
        ])
        out = self.handoff("away").stdout.splitlines()
        self.assertTrue(self.flag.is_file())
        self.assertTrue(out[0].startswith("away: since "))
        self.assertEqual(out[1], "permission s3: waits on a permission prompt; answer it from the phone")
        out = self.handoff("back").stdout.splitlines()
        self.assertFalse(self.flag.exists())
        self.assertEqual(out, ["away: off", "s3: waits on permission prompt"])
        self.assertFalse(self.calls.exists())


if __name__ == "__main__":
    unittest.main()
