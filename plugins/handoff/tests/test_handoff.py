"""Run with `python3 -m unittest discover plugins/handoff/tests`. Each test gets its own config
dir and temp dir, a stub `claude` that serves a session list and records what it was
called with, and a stub `tmux` that serves a pane list, so nothing reaches a real
session or the user's files."""
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
        self.env = {**os.environ, "CLAUDE_CONFIG_DIR": str(self.tmp / "claude"), "TMPDIR": str(self.tmp),
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

    def context(self, pct, prompt=None, calls=(), sid="s1", name="story-s3", requests=30, **extra):
        stats = self.tmp / "claude" / "session-stats"
        stats.mkdir(parents=True, exist_ok=True)
        if pct != "missing":
            (stats / f"{sid}.json").write_text(json.dumps({"session_id": sid, "session_name": name, "ctx_pct": pct}))
        transcript = self.tmp / f"{sid}.jsonl"
        transcript.write_text("".join(f'{{"requestId":"req_{i}"}}\n' for i in range(requests)))
        data = {"session_id": sid, "transcript_path": str(transcript), **extra}
        if prompt is not None:
            data.update(hook_event_name="UserPromptSubmit", prompt=prompt)
        else:
            data.update(hook_event_name="PostToolBatch", tool_calls=list(calls))
        out = self.handoff("context", stdin=json.dumps(data)).stdout
        if not out:
            return None
        hook = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(hook["hookEventName"], "UserPromptSubmit" if prompt is not None else "PostToolBatch")
        return hook["additionalContext"]

    def test_context_every_prompt_at_40(self):
        self.assertIsNone(self.context(39, "hi"))
        self.assertTrue(self.context(45, "hi").startswith("Context ~45%. Repo work: /handoff:close"))
        self.assertIn("/compact Keep:", self.context(45, "again"))
        self.assertIsNone(self.context(45), "the prompt told step 40")
        self.assertIn("~45%", self.context(45, "/handoff:away"), "away does not latch")

    def test_context_mid_run_steps(self):
        self.assertIsNotNone(self.context(41))
        self.assertIsNone(self.context(48))
        self.assertIn("~50%", self.context(50))
        self.assertIsNone(self.context(55))
        self.assertIn("~63%", self.context(63))
        self.assertIsNone(self.context(70))

    def test_context_quiet_from_a_close_until_the_next_prompt(self):
        self.assertIsNotNone(self.context(41))
        skill = {"tool_name": "Skill", "tool_input": {"skill": "handoff:close", "args": "spawn"}}
        self.assertIsNone(self.context(52, calls=[skill]))
        self.assertIsNone(self.context(61))
        self.assertIsNotNone(self.context(61, "next request"), "a close does not silence the prompt line")
        self.assertIsNone(self.context(62), "resumes at the step reached")
        self.assertIsNone(self.context(62, "/handoff:close spawn"))
        self.assertIsNone(self.context(65))

    def test_context_low_reading_clears_missing_does_not(self):
        self.assertIsNotNone(self.context(41))
        self.assertIsNone(self.context("missing"))
        self.assertIsNone(self.context(None))
        self.assertIsNone(self.context(42))
        self.assertIsNone(self.context(36))
        self.assertIsNone(self.context(42))
        self.assertIsNone(self.context(35))
        self.assertIsNotNone(self.context(42), "a reading of 35% or less clears the steps")

    def test_context_silent_for_subagents_reporters_and_cold_sessions(self):
        self.assertIsNone(self.context(50, "hi", agent_id="a1"))
        self.assertIsNone(self.context(50, "hi", name="story-report"))
        self.assertIsNone(self.context(50, "hi", requests=29))
        self.assertIsNotNone(self.context(50, "hi", name="story-coordinator"))
        self.assertIsNone(self.context("missing", "hi", sid="nostats"))


if __name__ == "__main__":
    unittest.main()
