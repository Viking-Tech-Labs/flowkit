"""Run with `python3 -m unittest discover plugins/ladder/tests`. Each test gets its own config
dirs and a stub `claude` that reports the plan, so nothing touches the user's files."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "ladder.py"


class Ladder(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "bin").mkdir()
        self.plan("max")
        self.env = {**os.environ, "XDG_CONFIG_HOME": str(self.tmp / "xdg"),
                    "CLAUDE_CONFIG_DIR": str(self.tmp / "claude"),
                    "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}"}
        (self.tmp / "claude").mkdir()
        self.file = self.tmp / "xdg" / "flowkit" / "ladder.md"

    def plan(self, name):
        stub = self.tmp / "bin" / "claude"
        stub.write_text(f"#!/bin/sh\necho '{json.dumps({'subscriptionType': name})}'\n")
        stub.chmod(0o755)

    def run_(self, *args, stdin=""):
        return subprocess.run(["python3", str(SCRIPT), *args], input=stdin, capture_output=True,
                              text=True, env=self.env)

    def gate(self, command):
        out = self.run_("gate", stdin=json.dumps({"tool_input": {"command": command}})).stdout
        return json.loads(out)["hookSpecificOutput"] if out else None

    def test_defaults_by_plan(self):
        self.assertIn("implementation: opus/medium", self.run_("plan").stdout)
        self.plan("pro")
        self.assertIn("implementation: sonnet/high", self.run_("plan").stdout)
        self.plan("")
        self.assertIn("plan: unknown", self.run_("plan").stdout)

    def test_diff_writes_nothing_and_write_does(self):
        self.assertIn("+| planning | opus | xhigh |", self.run_("diff").stdout)
        self.assertFalse(self.file.exists())
        self.run_("write")
        show = self.run_("show").stdout
        self.assertIn("managed: yes", show)
        self.assertIn("max: off", show)

    def test_edit_keeps_other_rows(self):
        self.run_("write")
        self.plan("pro")
        self.run_("write", "docs=haiku/low")
        show = self.run_("show").stdout
        self.assertIn("docs: haiku/low", show)
        self.assertIn("implementation: opus/medium", show)
        self.run_("write", "--reset")
        self.assertIn("implementation: sonnet/high", self.run_("show").stdout)

    def test_refusals(self):
        self.assertEqual(self.run_("write", "planning=fable/max").returncode, 2)
        self.assertEqual(self.run_("write", "nap=opus/high").returncode, 2)
        self.assertEqual(self.run_("write", "max=on", "planning=fable/max").returncode, 0)
        self.assertEqual(self.run_("write", "max=off").returncode, 2)

    def test_own_ladder_untouched(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text("# my own ladder\n\n## Rung\n\nmax: on\n")
        self.assertEqual(self.run_("write").returncode, 3)
        self.assertEqual(self.file.read_text(), "# my own ladder\n\n## Rung\n\nmax: on\n")
        self.assertIn("managed: no", self.run_("show").stdout)
        self.assertIsNone(self.gate("claude --effort max -p hi"))

    def test_gate(self):
        self.assertIsNone(self.gate("claude --effort max -p hi"))  # no table
        self.run_("write")
        self.assertIsNone(self.gate("claude --effort max -p hi"))  # max off
        self.run_("write", "max=on")
        self.assertEqual(self.gate("flowkit-spawn --effort max x")["permissionDecision"], "ask")
        self.assertEqual(self.gate("CLAUDE_CODE_EFFORT_LEVEL=max claude")["permissionDecision"], "ask")
        self.assertIsNone(self.gate("claude --effort xhigh -p hi"))
        self.assertIsNone(self.gate("cat ~/.claude/x --effort max"))
        (self.tmp / "claude" / "away").write_text("2026-10-17 02:00\n")
        out = self.gate("flowkit-spawn --effort 'max' x")
        self.assertEqual(out["permissionDecision"], "allow")
        self.assertEqual(out["updatedInput"]["command"], "flowkit-spawn --effort 'xhigh' x")


if __name__ == "__main__":
    unittest.main()
