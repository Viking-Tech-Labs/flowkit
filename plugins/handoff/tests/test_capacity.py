"""Run with `python3 -m unittest discover plugins/handoff/tests`. Each test gets its own config dir,
claude dir and temp git repos, and a stub `claude` whose `agents` answer the test sets (a list, a
failure or a hang), so nothing reaches a real session or the user's files."""
import importlib.util
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
