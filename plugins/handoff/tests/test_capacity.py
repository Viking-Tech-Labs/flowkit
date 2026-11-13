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
FIXTURE = PLUGIN / "tests" / "fixtures" / "capacity-status.json"  # the board's tests read it too
spec = importlib.util.spec_from_file_location("capacity", CAPACITY)
capacity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capacity)


def shape(value):
    """The keys of a JSON value, all the way down, with every list's items merged."""
    if isinstance(value, dict):
        return {k: shape(v) for k, v in value.items()}
    if isinstance(value, list):
        merged = {}
        for item in value:
            item = shape(item)
            if isinstance(item, dict):
                for k, v in item.items():
                    merged[k] = v if merged.get(k) in (None, "*") else merged[k]
        return [merged]
    return "*"


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

    def cli(self, *args, at=None):
        env = {**os.environ, **({"FLOWKIT_CAPACITY_NOW": str(at)} if at else {})}
        return subprocess.run(["python3", str(CAPACITY), *args], capture_output=True, text=True, env=env)

    def log_events(self):
        path = self.claude / "capacity" / "log.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def held(self):
        return {p.stem: json.loads(p.read_text())["session"]
                for p in (self.claude / "capacity" / "resources").glob("*.json")}

    def devices(self):
        app = self.repo("app")
        self.conf.write_text(f"[{app}]\nresource simulator = a device check\nresource emulator = a device check\n")
        return app

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


    def test_two_processes_taking_at_once(self):
        app = self.devices()
        ids = [f"{i:08x}-1111-1111-1111-111111111111" for i in range(6)]
        self.agents([self.row(f"s{i}", sid, app) for i, sid in enumerate(ids)])
        env = {**os.environ}
        procs = [subprocess.Popen(["python3", str(CAPACITY), "take", "simulator,emulator", "--session", sid],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env) for sid in ids]
        codes = [p.wait() for p in procs]
        for p in procs:
            p.stdout.close()
            p.stderr.close()
        self.assertEqual(sorted(codes), [0, 1, 1, 1, 1, 1])
        winner = ids[codes.index(0)]
        self.assertEqual(self.held(), {"simulator": winner, "emulator": winner})
        self.assertEqual([e["event"] for e in self.log_events()], ["take", "take"])

    def test_all_or_nothing_and_the_union_rule(self):
        app = self.devices()
        a, b = "a" * 8 + "-2222-2222-2222-" + "2" * 12, "b" * 8 + "-2222-2222-2222-" + "2" * 12
        self.agents([self.row("demo-s1", a, app), self.row("demo-s2", b, app)])
        self.assertEqual(self.cli("take", "simulator", "--session", a).returncode, 0)
        r = self.cli("take", "simulator", "emulator", "--session", b)
        self.assertEqual(r.returncode, 1)
        self.assertIn("simulator is held by demo-s1", r.stderr)
        self.assertEqual(self.held(), {"simulator": a})  # b got nothing, not even the free emulator
        r = self.cli("take", "emulator", "--session", a)
        self.assertEqual(r.returncode, 3)
        self.assertIn("holding simulator outside the set", r.stderr)
        self.assertEqual(self.cli("take", "simulator,emulator", "--session", a).returncode, 0)
        self.assertEqual(self.held(), {"simulator": a, "emulator": a})
        self.assertEqual(self.cli("take", "printer", "--session", a).returncode, 2)
        self.assertEqual(self.cli("release", "emulator", "--session", a).returncode, 0)
        self.assertEqual(self.held(), {"simulator": a})
        self.cli("release", "--session", a)
        self.assertEqual(self.held(), {})
        self.assertEqual([e["event"] for e in self.log_events()], ["take", "take", "release", "release"])

    def test_free_rules(self):
        app = self.devices()
        idle, closed, dead, other = (f"{c * 8}-3333-3333-3333-333333333333" for c in "abcd")
        rows = [self.row("demo-s1", idle, app, "idle"), self.row("demo-s2", closed, app, "idle"),
                self.row("x", dead, app), self.row("y", other, app)]
        self.agents(rows)
        t = time.time()
        self.assertEqual(self.cli("take", "simulator", "--session", idle, at=t).returncode, 0)
        self.assertEqual(self.cli("take", "emulator", "--session", closed, at=t).returncode, 0)
        self.assertEqual(self.cli("take", "simulator", "--session", other, at=t + 10 * 60).returncode, 1)
        self.story("demo", f"## demo-s2 · 2026-11-13 10:00\nSession: {closed}\n")
        self.agents([], "fail")  # liveness unknown: nothing goes, not even the closed holder's
        self.assertEqual(self.cli("take", "simulator,emulator", "--session", other, at=t + 5 * 3600).returncode, 1)
        self.assertEqual(self.held(), {"simulator": idle, "emulator": closed})
        self.agents(rows)
        self.assertEqual(self.cli("take", "simulator,emulator", "--session", other, at=t + 31 * 60).returncode, 0)
        frees = {e["resource"]: e["reason"] for e in self.log_events() if e["event"] == "free"}
        self.assertEqual(frees, {"simulator": "idle", "emulator": "closed"})
        self.agents(rows[:3])  # other's pid is gone
        self.assertEqual(self.cli("take", "simulator", "--session", dead, at=t + 32 * 60).returncode, 0)
        reclaim = [e for e in self.log_events() if e["event"] == "reclaim"]
        self.assertEqual([(e["resource"], e["session"], e["holder"]) for e in reclaim],
                         [("emulator", other, "y"), ("simulator", other, "y")])

    def test_the_users_test_hold(self):
        app = self.devices()
        a = "a" * 8 + "-4444-4444-4444-" + "4" * 12
        self.agents([self.row("demo-s1", a, app)])
        t = time.time()
        r = self.cli("hold", "simulator", "--for", "me", "--minutes", "60", at=t)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.cli("take", "simulator", "--session", a, at=t + 59 * 60).returncode, 1)
        self.cli("hold", "simulator", "--for", "me", "--minutes", "60", at=t + 59 * 60)  # renewed
        self.agents([], "hang")  # a test hold runs out on the clock alone
        self.assertEqual(self.cli("take", "simulator", "--session", a, at=t + 100 * 60).returncode, 1)
        self.assertEqual(self.cli("take", "simulator", "--session", a, at=t + 120 * 60).returncode, 0)
        self.assertEqual([(e["event"], e.get("reason")) for e in self.log_events()],
                         [("hold", None), ("hold", None), ("free", "expired"), ("take", None)])
        self.assertEqual(self.cli("hold", "simulator", "--for", "me").returncode, 1)
        self.assertEqual(self.cli("release", "--for", "me").stdout.strip(), "held nothing")

    def test_a_busy_lock_is_waited_for_then_gone_on_without(self):
        self.devices()
        (self.claude / "capacity").mkdir(parents=True)
        holder = subprocess.Popen(["python3", "-c", "import fcntl,sys,time; f=open(sys.argv[1],'a');"
                                   "fcntl.flock(f, fcntl.LOCK_EX); print('locked', flush=True); time.sleep(30)",
                                   str(self.claude / "capacity" / ".lock")], stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.kill)
        holder.stdout.readline()
        started = time.time()
        r = subprocess.run(["python3", str(CAPACITY), "take", "simulator", "--session", "x" * 36], capture_output=True,
                           text=True, env={**os.environ, "FLOWKIT_CAPACITY_LOCK_WAIT": "1"})
        self.assertGreaterEqual(time.time() - started, 1)
        self.assertEqual(r.returncode, 0)
        self.assertIn("going on without it", r.stderr)
        holder.kill()
        holder.wait()
        holder.stdout.close()


    def test_status_and_status_json(self):
        app = self.devices()
        self.conf.write_text(self.conf.read_text() + "slots = 1\n")
        tree = self.tmp / "app-wt"
        git("-C", str(app), "worktree", "add", "-q", str(tree), "-b", "s2")
        lib = self.repo("lib")
        s2, review = "a" * 8 + "-5555-5555-5555-" + "5" * 12, "b" * 8 + "-5555-5555-5555-" + "5" * 12
        self.story("demo", "> launched demo-s2\n> launched demo-review\n")
        self.agents([self.row("demo-s2", s2, tree), self.row("demo-review", review, tree, "idle")])
        launching = self.claude / "capacity" / "launching"
        launching.mkdir(parents=True)
        (launching / f"{os.getpid()}.json").write_text(json.dumps({"tree": str(lib), "story": "side"}))
        t = time.time()
        self.assertEqual(self.cli("take", "simulator", "--session", s2, at=t).returncode, 0)
        self.assertEqual(self.cli("hold", "emulator", "--for", "me", "--minutes", "30", at=t).returncode, 0)
        r = self.cli("status", at=t)
        self.assertEqual(r.returncode, 0, r.stderr)
        hm = time.strftime("%H:%M", time.localtime(t))
        until = time.strftime("%H:%M", time.localtime(t + 30 * 60))
        self.assertEqual(r.stdout.splitlines(), [
            "slots 2/3",
            f"repo {app} · 1/1",
            f"chain demo · running · {tree} · demo-s2 (busy), demo-review (idle)",
            f"chain side · launching · {lib} · flowkit-spawn pid {os.getpid()}",
            f"resource emulator · held for you until {until} (test hold)",
            f"resource simulator · held by demo-s2 (demo) since {hm} (take)"])
        data = json.loads((self.claude / "capacity" / "status.json").read_text())
        self.assertEqual(data["version"], capacity.STATUS_VERSION)
        self.assertEqual((data["agents"], data["used"], data["slots"]), ("known", 2, 3))
        self.assertEqual([(c["story"], c["state"]) for c in data["chains"]], [("demo", "running"), ("side", "launching")])
        self.assertEqual({r["name"]: r["session"] for r in data["resources"]}, {"simulator": s2, "emulator": "me"})
        self.assertEqual(json.loads(self.cli("status", "--json", at=t).stdout), data)
        fixture = json.loads(FIXTURE.read_text())
        self.assertEqual(fixture["version"], capacity.STATUS_VERSION)
        self.assertEqual(shape(fixture), shape(data))  # change the fixture with the schema, and the board with it
        self.assertEqual([e["event"] for e in self.log_events()], ["take", "hold"])
        self.agents([], "fail")
        r = self.cli("status", at=t)
        self.assertTrue(r.stdout.startswith("slots ?/3 · claude agents did not answer"))
        data = json.loads((self.claude / "capacity" / "status.json").read_text())
        self.assertEqual((data["agents"], data["used"], data["chains"]), ("unknown", None, []))
        self.assertEqual(len(data["resources"]), 2)

    def test_the_wrapper_runs_status_with_nothing_configured(self):
        r = subprocess.run([str(PLUGIN / "bin" / "flowkit-capacity"), "status"], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "slots 0/3\n", ""))
        self.assertTrue((self.claude / "capacity" / "status.json").exists())


if __name__ == "__main__":
    unittest.main()
