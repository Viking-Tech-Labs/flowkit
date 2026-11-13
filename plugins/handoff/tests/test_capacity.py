"""Run with `python3 -m unittest discover plugins/handoff/tests`. Each test gets its own config dir,
claude dir and temp git repos, and a stub `claude` whose `agents` answer the test sets (a list, a
failure or a hang), so nothing reaches a real session or the user's files."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parent.parent
CAPACITY = PLUGIN / "scripts" / "capacity.py"
spec = importlib.util.spec_from_file_location("capacity", CAPACITY)
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)


def git(*args):
    subprocess.run(["git", *args], check=True, capture_output=True)


class Capacity(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp()))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        (self.tmp / "xdg" / "flowkit").mkdir(parents=True)
        self.conf = self.tmp / "xdg" / "flowkit" / "capacity.conf"
        self.claude = self.tmp / "claude"
        (self.claude / "stories").mkdir(parents=True)
        (self.tmp / "bin").mkdir()
        self.env = {"CLAUDE_CONFIG_DIR": str(self.claude), "XDG_CONFIG_HOME": str(self.tmp / "xdg"),
                    "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}", "FLOWKIT_CAPACITY_AGENTS_TIMEOUT": "1"}
        patch = mock.patch.dict(os.environ, self.env)
        patch.start()
        self.addCleanup(patch.stop)
        self.agents([])

    def agents(self, rows, mode="list"):
        """The stub `claude agents`: lists rows, fails, hangs or answers in an unknown shape."""
        (self.tmp / "agents.json").write_text(json.dumps(rows))
        body = {"list": f"cat {self.tmp / 'agents.json'}", "fail": "echo boom >&2; exit 1",
                "hang": "exec sleep 30", "shape": "echo '{\"sessions\": []}'"}[mode]
        stub = self.tmp / "bin" / "claude"
        stub.write_text(f"#!/bin/sh\nif [ \"$1\" = agents ]; then {body}; fi\n")
        stub.chmod(0o755)

    def row(self, name, sid, cwd, status="busy", pid=4242):
        return {"id": sid[:8], "sessionId": sid, "name": name, "cwd": str(cwd), "kind": "interactive",
                "status": status, "pid": pid, "startedAt": int(time.time() * 1000)}

    def story(self, slug, text):
        (self.claude / "stories" / f"{slug}.md").write_text(text)

    def repo(self, name):
        path = self.tmp / name
        path.mkdir()
        git("-C", str(path), "init", "-q")
        git("-C", str(path), "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q",
            "--allow-empty", "-m", "init")
        return path

    def test_no_file_is_three_slots_and_no_resources(self):
        cfg = capacity.load_config(self.conf)
        self.assertEqual(cfg["slots"], 3)
        self.assertEqual(cfg["repos"], {})
        self.assertEqual(capacity.declared(cfg), set())

    def test_machine_keys_only(self):
        self.conf.write_text("slots = 5   # more\nslice_minutes = 40\ncoordinator_growth = 250k\n")
        cfg = capacity.load_config(self.conf)
        self.assertEqual((cfg["slots"], cfg["slice_minutes"], cfg["coordinator_growth"]), (5, 40, 250_000))
        self.assertEqual(cfg["repos"], {})

    def test_repo_section_with_resources(self):
        app = self.repo("app")
        self.conf.write_text(f"slots = 4\n\n[{app}]\nslots = 2\nresource simulator = install a build\n"
                             "resource backend  = start the local stack\n")
        cfg = capacity.load_config(self.conf)
        section = capacity.repo_section(cfg, app)
        self.assertEqual(section["slots"], 2)
        self.assertEqual(section["resources"], {"simulator": "install a build", "backend": "start the local stack"})
        self.assertEqual(capacity.declared(cfg), {"simulator", "backend"})
        self.assertIsNone(capacity.repo_section(cfg, self.repo("other")))

    def test_a_worktree_inherits_its_main_checkouts_section(self):
        app = self.repo("app")
        tree = self.tmp / "app-wt"
        git("-C", str(app), "worktree", "add", "-q", str(tree), "-b", "side")
        self.conf.write_text(f"[{app}]\nresource emulator = run a device check\n")
        cfg = capacity.load_config(self.conf)
        self.assertEqual(capacity.main_checkout(tree), str(app))
        self.assertEqual(capacity.repo_section(cfg, tree)["resources"], {"emulator": "run a device check"})

    def test_a_bad_line_is_skipped(self):
        self.conf.write_text("slots = many\nresource orphan = x\nnonsense\nslots = 2\n")
        r = subprocess.run(["python3", "-c", f"import importlib.util as u; s=u.spec_from_file_location('c', '{CAPACITY}');"
                            f"m=u.module_from_spec(s); s.loader.exec_module(m); print(m.load_config(m.Path('{self.conf}'))['slots'])"],
                           capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), "2")
        self.assertEqual(len(r.stderr.strip().splitlines()), 3)

    def test_liveness_is_three_valued(self):
        sid = "a" * 8 + "-0000-0000-0000-" + "0" * 12
        self.agents([self.row("x", sid, self.tmp)])
        rows = capacity.snapshot()
        self.assertEqual(capacity.liveness(rows, sid), "live")
        self.assertEqual(capacity.liveness(rows, "b" * 36), "dead")
        for mode in ("fail", "hang", "shape"):
            self.agents([], mode)
            started = time.time()
            self.assertIsNone(capacity.snapshot(), mode)
            self.assertLess(time.time() - started, 5, mode)
            self.assertEqual(capacity.liveness(None, sid), "unknown")
            self.assertIsNone(capacity.chains(None))
            self.assertIsNone(capacity.slots(capacity.load_config(), None))

    def test_slot_count(self):
        app, lib, other = self.repo("app"), self.repo("lib"), self.repo("other")
        tree = self.tmp / "app-wt"
        git("-C", str(app), "worktree", "add", "-q", str(tree), "-b", "s2")
        (app / "sub").mkdir()
        ids = {n: f"{i:08x}-0000-0000-0000-000000000000" for i, n in enumerate(
            ["demo-s1", "demo-s2", "demo-review", "demo-report", "demo-coord", "lone", "demo-s0", "side-s1", "back"])}
        self.story("demo", f"> launched demo-review\n\n## demo-s1 · 2026-11-13 10:00\nSession: {ids['demo-s1']}\n"
                           f"Next: started demo-s2 (opus/high)\n\n## demo-s0 · 2026-11-12 09:00\n"
                           f"Session: {ids['demo-s0']}\nNext: started demo-s1\n")
        self.story("side", "> launched side-s1\n> launched back\n## back · 2026-11-13 08:00\n"
                           f"Session: {ids['back']}\n")
        self.story("demo.tests", "> launched lone\n")
        rows = [self.row("demo-s1", ids["demo-s1"], app, "idle"),  # closed and idle: out
                self.row("demo-s2", ids["demo-s2"], tree / ".", "busy"),  # the next slice in a worktree
                self.row("demo-review", ids["demo-review"], tree, "idle"),  # same tree: shares the slot
                self.row("demo-report", ids["demo-report"], app),  # reporter: out
                self.row("demo-coord", ids["demo-coord"], app),  # coordinator: out
                self.row("lone", ids["lone"], other),  # in no story: out
                self.row("demo-s0", ids["demo-s0"], app, pid=None),  # no pid: out
                self.row("side-s1", ids["side-s1"], lib, "idle"),  # stalled below
                self.row("back", ids["back"], app / "sub", "busy")]  # closed but busy again: counts
        self.agents(rows)
        transcript = self.claude / "projects" / "-x" / f"{ids['side-s1']}.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("{}\n")
        old = time.time() - 3 * 3600
        os.utime(transcript, (old, old))
        self.conf.write_text(f"[{app}]\nslots = 1\n")
        found = capacity.chains(capacity.snapshot())
        by_tree = {c["tree"]: c for c in found}
        self.assertEqual(set(by_tree), {str(tree), str(lib), str(app)})
        self.assertEqual(sorted(s["name"] for s in by_tree[str(tree)]["sessions"]), ["demo-review", "demo-s2"])
        self.assertEqual((by_tree[str(tree)]["story"], by_tree[str(tree)]["repo"]), ("demo", str(app)))
        self.assertEqual(by_tree[str(lib)]["state"], "stalled")
        self.assertEqual([s["name"] for s in by_tree[str(app)]["sessions"]], ["back"])
        used = capacity.slots(capacity.load_config(), found)
        self.assertEqual((used["slots"], used["used"]), (3, 2))
        self.assertEqual(used["repos"], {str(app): {"slots": 1, "used": 2}})

    def test_a_launching_marker_holds_its_tree_while_its_pid_lives(self):
        app = self.repo("app")
        launching = self.claude / "capacity" / "launching"
        launching.mkdir(parents=True)
        (launching / f"{os.getpid()}.json").write_text(json.dumps({"tree": str(app), "story": "demo"}))
        dead = subprocess.Popen(["true"])
        dead.wait()
        (launching / f"{dead.pid}.json").write_text(json.dumps({"tree": str(self.repo("lib")), "story": "x"}))
        found = capacity.chains(capacity.snapshot())
        self.assertEqual([(c["tree"], c["state"], c["story"]) for c in found], [(str(app), "launching", "demo")])


if __name__ == "__main__":
    unittest.main()
