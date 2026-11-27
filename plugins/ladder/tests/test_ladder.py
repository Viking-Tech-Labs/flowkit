"""Run with `python3 -m unittest discover plugins/ladder/tests`. Each test gets its own config
dirs and a stub `claude` that reports the plan, so nothing touches the user's files."""
import json
import os
import subprocess
import tempfile
import time
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
        self.assertEqual(self.run_("show").stdout.splitlines()[1:], ["managed: no", "max: on"])

    def test_gate_honours_max_on_in_a_ladder_of_your_own(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text("# my own ladder\n\n## Rung\n\nPick by kind.\n\nmax: on\n")
        self.assertEqual(self.gate("flowkit-spawn --effort max x")["permissionDecision"], "ask")
        self.assertIsNone(self.gate("flowkit-spawn --effort xhigh x"))
        (self.tmp / "claude" / "away").write_text(time.strftime("%Y-%m-%d %H:%M") + "\n")
        out = self.gate("flowkit-spawn --effort max x")
        self.assertEqual((out["permissionDecision"], out["updatedInput"]["command"]), ("allow", "flowkit-spawn --effort xhigh x"))

    def test_a_missing_max_line_switches_the_gate_off_only_in_the_plugins_own_table(self):
        launch = "claude --effort max -p hi"
        asks = lambda: (self.gate(launch) or {}).get("permissionDecision") == "ask"
        self.assertFalse(asks(), "no ladder at all")
        self.file.parent.mkdir(parents=True)
        self.file.write_text("# my own ladder\n\n## Rung\n\nA `max` pick needs a yes. Planning: opus/max: only on request.\n")
        self.assertTrue(asks(), "a ladder of your own that never heard of the line")
        self.assertEqual(self.run_("show").stdout.splitlines()[1:], ["managed: no", "max: on"])
        self.file.write_text(self.file.read_text() + "\nmax: off\n")
        self.assertFalse(asks(), "your own ladder says off")
        self.file.unlink()
        self.run_("write", "max=on")
        self.assertTrue(asks())
        self.file.write_text(self.file.read_text().replace("\nmax: on\n", "\n"))
        self.assertFalse(asks(), "the plugin's own table without the line: off, as it always was")
        self.assertIn("max: off", self.run_("show").stdout)

    def test_gate(self):
        self.assertIsNone(self.gate("claude --effort max -p hi"))  # no table
        self.run_("write")
        self.assertIsNone(self.gate("claude --effort max -p hi"))  # max off
        self.run_("write", "max=on")
        self.assertEqual(self.gate("flowkit-spawn --effort max x")["permissionDecision"], "ask")
        self.assertEqual(self.gate("CLAUDE_CODE_EFFORT_LEVEL=max claude")["permissionDecision"], "ask")
        self.assertIsNone(self.gate("claude --effort xhigh -p hi"))
        self.assertIsNone(self.gate("cat ~/.claude/x --effort max"))
        ahead = time.strftime("%Y-%m-%d %H:%M", time.localtime(time.time() + 300))
        (self.tmp / "claude" / "away").write_text(f"{ahead}\nby: coord-a\n")
        self.assertEqual(self.gate("flowkit-spawn --effort max x")["permissionDecision"], "allow",
                         "set and not yet in effect: an overnight close launches within those minutes")
        old = time.time() - 13 * 3600
        (self.tmp / "claude" / "away").write_text("2026-10-17 02:00\n")
        os.utime(self.tmp / "claude" / "away", (old, old))
        self.assertEqual(self.gate("flowkit-spawn --effort max x")["permissionDecision"], "ask", "an expired flag")
        os.utime(self.tmp / "claude" / "away")
        out = self.gate("flowkit-spawn --effort 'max' x")
        self.assertEqual(out["permissionDecision"], "allow")
        self.assertEqual(out["updatedInput"]["command"], "flowkit-spawn --effort 'xhigh' x")


if __name__ == "__main__":
    unittest.main()
