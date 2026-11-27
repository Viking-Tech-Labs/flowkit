"""Run with `python3 -m unittest discover plugins/board/tests`: drives bin/flowkit-board against a
config folder of the test's own and a stub `claude`. Skipped where neither bun nor a Node that runs
TypeScript is on PATH; what the JSON holds is tested in board.test.ts."""
import json
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

BOARD = Path(__file__).resolve().parent.parent / "bin" / "flowkit-board"
STORY = "## a-s1 · 2026-10-16 10:00\nNext: started a-s2\n\nFOR ME\n1. Push main.\n"
AGENTS = [{"kind": "interactive", "status": "busy", "name": "a-s2", "sessionId": "x1", "cwd": "/w/app"}]


def runtime():
    """The folder of a runtime flowkit-board can use, or None."""
    node = shutil.which("node")
    if shutil.which("bun"):
        return str(Path(shutil.which("bun")).parent)
    if node and subprocess.run([node, "-e", "process.exit(process.features.typescript ? 0 : 1)"]).returncode == 0:
        return str(Path(node).parent)
    return None


@unittest.skipUnless(runtime(), "needs bun, or Node 22.18 or newer")
class BoardCommand(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.claude = self.tmp / "claude"
        (self.tmp / "bin").mkdir()
        self.env = {"HOME": str(self.tmp), "CLAUDE_CONFIG_DIR": str(self.claude),
                    "PATH": f"{self.tmp / 'bin'}:{runtime()}:/usr/bin:/bin"}

    def stub(self, body):
        path = self.tmp / "bin" / "claude"
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def board(self, *args, agents=None):
        return subprocess.run([str(BOARD), *args], input=None if agents is None else json.dumps(agents),
                              capture_output=True, text=True, env=self.env)

    def snapshot(self, *args, agents=None):
        out = self.board("snapshot", *args, agents=agents)
        self.assertEqual((out.returncode, out.stderr), (0, ""))
        return json.loads(out.stdout)

    def test_it_reads_what_the_board_reads(self):
        (self.claude / "stories").mkdir(parents=True)
        (self.claude / "stories" / "a.md").write_text(STORY)
        (self.claude / "stories" / "a.tests.md").write_text("# a test queue\n")
        (self.claude / "stories" / "b.md").write_text("a draft, no close section\n")
        (self.claude / "session-stats").mkdir()
        (self.claude / "session-stats" / "x1.json").write_text('{"ctx_pct": 33.6, "cost_usd": 2.5}')
        out = self.snapshot("--agents", "-", agents=AGENTS)
        self.assertEqual((out["version"], out["isAgentsKnown"], out["isStoriesKnown"], out["loose"]),
                         (1, True, True, []))
        (story,) = out["stories"]
        self.assertEqual((story["story"], story["state"], story["why"], story["forMeFrom"], story["members"]),
                         ("a", "running", "a-s2 working", "a-s1", ["a-report", "a-s1", "a-s2"]))
        self.assertEqual(story["sessions"], [{"id": "x1", "name": "a-s2", "state": "working", "ctx": 34,
                                              "cost": 2.5, "repo": "app", "isReporter": False}])
        self.assertEqual(story["steps"], [{"key": "a|a-s1|push main.", "text": "Push main.", "from": "a-s1"}])
        (self.claude / "flowkit").mkdir()
        (self.claude / "flowkit" / "parked.json").write_text(json.dumps(
            {"at": time.time(), "panes": [{"story": "a", "line": "Trust this folder?"}]}))
        (story,) = self.snapshot("--agents", "-", agents=AGENTS)["stories"]
        self.assertEqual((story["state"], story["why"]), ("needs-you", "parked at a start-up prompt: Trust this folder?"))

    def test_the_sessions_come_from_claude_unless_handed_in(self):
        self.stub(f"[ \"$*\" = 'agents --json --all' ] && echo '{json.dumps(AGENTS)}'")
        out = self.snapshot()
        self.assertEqual((out["isAgentsKnown"], out["stories"], [c["name"] for c in out["loose"]]),
                         (True, [], ["a-s2"]), "no stories folder yet is no story")
        self.stub("exit 1")
        self.assertEqual(self.snapshot()["isAgentsKnown"], False)
        self.assertEqual(self.snapshot("--agents", "-", agents=[])["isAgentsKnown"], True)

    def test_anything_else_prints_the_usage(self):
        for args in ([], ["cards"], ["snapshot", "--agents"], ["snapshot", "--json"]):
            out = self.board(*args)
            self.assertEqual((out.returncode, out.stdout), (2, ""))
            self.assertIn("flowkit-board snapshot [--agents -]", out.stderr)


if __name__ == "__main__":
    unittest.main()
