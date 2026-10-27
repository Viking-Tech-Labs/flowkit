"""Run with `python3 -m unittest discover plugins/prompt-audit/tests`. Each test gets its own config
dir, data dir, transcripts dir and out dir under a temp dir, and drives extract.py through its CLI,
so nothing reads the user's transcripts or writes outside the temp dir."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

EXTRACT = Path(__file__).resolve().parent.parent / "scripts" / "extract.py"
PROJECT = "-Users-me-repo"


class Extract(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.transcripts = self.tmp / "projects" / PROJECT
        self.transcripts.mkdir(parents=True)
        (self.tmp / "claude").mkdir()
        self.env = {**os.environ, "CLAUDE_CONFIG_DIR": str(self.tmp / "claude"), "HOME": str(self.tmp)}
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def transcript(self, sid, *records):
        with open(self.transcripts / f"{sid}.jsonl", "w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps({**record, "sessionId": sid}) + "\n")

    @staticmethod
    def prompt(uuid, text, ts="2026-10-23T10:00:00.000Z", branch="main"):
        return {"type": "user", "uuid": uuid, "timestamp": ts, "gitBranch": branch, "cwd": "/Users/me/repo",
                "message": {"role": "user", "content": text}}

    @staticmethod
    def marker(new):
        return {"type": "continued-in", "timestamp": "2026-10-23T11:00:00.000Z", "continuedInSessionId": new}

    def extract(self):
        done = subprocess.run(["python3", str(EXTRACT), "run", str(self.tmp / "data"), str(self.tmp / "out"),
                               "--since", "2025-01-01", "--transcripts", str(self.tmp / "projects")],
                              capture_output=True, text=True, env=self.env, timeout=20)
        self.assertEqual(done.returncode, 0, done.stderr)
        out = self.tmp / "out"
        rows = [json.loads(line) for line in (out / "prompts.jsonl").read_text(encoding="utf-8").splitlines()]
        window = json.loads((out / "window.json").read_text(encoding="utf-8"))
        counts = re.search(r"continued sessions: (\d+) \((\d+) prompts merged\), fork copies dropped: (\d+)",
                           done.stdout)
        self.assertIsNotNone(counts, done.stdout)
        return rows, window, tuple(int(n) for n in counts.groups())

    def test_a_continuation_counts_once_and_the_original_row_wins(self):
        # the original sorts after its continuation by path, so the order comes from the marker
        self.transcript("b-original", self.prompt("u1", "first", "2026-10-23T10:00:00.000Z", "feature"),
                        self.prompt("u2", "second", "2026-10-23T10:05:00.000Z", "feature"), self.marker("a-resumed"))
        self.transcript("a-resumed", {"type": "agent-name", "agentName": "resumed"},
                        self.prompt("u1", "first", "2026-10-23T10:00:00.000Z", "main"),
                        self.prompt("u2", "second", "2026-10-23T10:05:00.000Z", "main"),
                        self.prompt("u3", "third", "2026-10-23T12:00:00.000Z", "main"))
        rows, window, counts = self.extract()
        self.assertEqual([(r["text"], r["session"], r["branch"]) for r in rows],
                         [("first", "a-resumed", "feature"), ("second", "a-resumed", "feature"),
                          ("third", "a-resumed", "main")])
        self.assertEqual(window["sessions"], 1)
        self.assertEqual(counts, (1, 2, 0))
        self.assertNotIn("b-original", next((self.tmp / "out").glob("*.txt")).read_text(encoding="utf-8"))

    def test_a_fork_without_a_marker_keeps_the_shared_prompt_once(self):
        self.transcript("a-fork", self.prompt("u1", "shared"), self.prompt("u2", "mine"))
        self.transcript("b-fork", self.prompt("u1", "shared"), self.prompt("u3", "yours"))
        rows, window, counts = self.extract()
        self.assertEqual(sorted(r["text"] for r in rows), ["mine", "shared", "yours"])
        self.assertEqual(window["sessions"], 2)
        self.assertEqual(counts, (0, 0, 1))

    def test_a_prompt_repeated_in_one_session_is_still_dropped(self):
        self.transcript("a-original", self.prompt("u1", "report now"), self.prompt("u2", "report now"),
                        self.marker("b-resumed"))
        self.transcript("b-resumed", self.prompt("u1", "report now"), self.prompt("u2", "report now"),
                        self.prompt("u3", "report now"), self.prompt("u4", "other"))
        rows, window, counts = self.extract()
        self.assertEqual([(r["text"], r["session"]) for r in rows],
                         [("report now", "b-resumed"), ("other", "b-resumed")])
        self.assertEqual(counts, (1, 2, 0))

    def test_a_dangling_marker_is_not_followed(self):
        self.transcript("a-original", self.prompt("u1", "kept"), self.marker("gone"))
        rows, window, counts = self.extract()
        self.assertEqual([(r["text"], r["session"]) for r in rows], [("kept", "a-original")])
        self.assertEqual(window["sessions"], 1)
        self.assertEqual(counts, (0, 0, 0))

    def test_a_marker_cycle_terminates(self):
        self.transcript("a-one", self.prompt("u1", "looped"), self.marker("b-two"))
        self.transcript("b-two", self.prompt("u1", "looped"), self.marker("a-one"))
        rows, window, counts = self.extract()
        self.assertEqual([r["text"] for r in rows], ["looped"])


if __name__ == "__main__":
    unittest.main()
