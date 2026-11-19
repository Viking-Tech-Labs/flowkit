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
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parent.parent
CAPACITY = PLUGIN / "scripts" / "capacity.py"
FIXTURE = PLUGIN.parent / "board" / "tests" / "fixtures" / "capacity-status.ts"  # the board reads it in its own tests
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
                    "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}", "FLOWKIT_CAPACITY_AGENTS_TIMEOUT": "3"}
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

    def arm(self, name, story, t, **more):
        """An open watch, as scripts/watch.py keeps it: only what a status line reads of an arm file."""
        path = self.claude / "flowkit" / "watches" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "name": name, "story": story, "state": "pending", "phase": "pipeline", "sha": "1" * 40, "armed": t - 600,
            "url": f"https://gitlab.com/g/p/-/merge_requests/{name.partition('!')[2]}", "polled": t - 60,
            "answer": "pipeline running", "on_green": {"run": "", "prompt": ""}, "rev": 3, **more}))
        return path

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
        (self.claude / "capacity" / "queue").mkdir()
        (self.claude / "capacity" / "queue" / "7.json").write_text(json.dumps(
            {"name": "next-s1", "story": "next", "tree": str(lib), "prompt": "/p.md", "argv": ["/p.md"],
             "needs": ["simulator"], "left": 2, "continuation": False, "queued": t, "by": "coordinator",
             "why": "slots 2/3", "eta": t + 3000}))
        self.arm("app!12", "demo", t)
        self.arm("lib!9", "side", t, state="fixing", answer="pipeline failed", red={"what": "pipeline failed"},
                 fix={"name": "side-fix-1", "state": "started", "at": t - 300, "tree": str(lib)})
        self.arm("lib!10", "next", t, state="waiting-base", waits={"base": "lib!9", "story": "side", "branch": "s9"})
        self.arm("lib!11", "next", t, phase="merge", sha="", polled=None, answer="")
        (self.claude / "flowkit" / "watches" / ".lib!12.json.4242").write_text("{")  # a write caught halfway
        (self.claude / "flowkit" / "watches" / "poller.pid").write_text("4242")
        r = self.cli("status", at=t)
        self.assertEqual(r.returncode, 0, r.stderr)
        hm = time.strftime("%H:%M", time.localtime(t))
        until = time.strftime("%H:%M", time.localtime(t + 30 * 60))
        armed, asked = (time.strftime("%H:%M", time.localtime(t - s)) for s in (600, 60))
        mr = "https://gitlab.com/g/p/-/merge_requests"
        self.assertEqual(r.stdout.splitlines(), [
            "slots 2/3",
            f"repo {app} · 1/1",
            f"chain demo · running · {tree} · demo-s2 (busy), demo-review (idle)",
            f"chain side · launching · {lib} · flowkit-spawn pid {os.getpid()}",
            f"resource emulator · held for you until {until} (test hold)",
            f"resource simulator · held by demo-s2 (demo) since {hm} (take)",
            f"queue #1 next-s1 (next) · slots 2/3 · needs simulator · queued {hm} by coordinator · "
            f"~{time.strftime('%H:%M', time.localtime(t + 3000))}",
            f"watch app!12 (demo) · pending · sha 1111111 · armed {armed} · asked {asked}: pipeline running · {mr}/12",
            f"watch lib!10 (next) · waiting-base (lib!9) · sha 1111111 · armed {armed} · asked {asked}: pipeline running · {mr}/10",
            f"watch lib!11 (next) · pending (merge) · armed {armed} · {mr}/11",
            f"watch lib!9 (side) · fixing (side-fix-1 started) · sha 1111111 · armed {armed} · asked {asked}: pipeline failed · {mr}/9"])
        data = json.loads((self.claude / "capacity" / "status.json").read_text())
        self.assertEqual(data["version"], capacity.STATUS_VERSION)
        self.assertEqual((data["agents"], data["used"], data["slots"]), ("known", 2, 3))
        self.assertEqual([(c["story"], c["state"]) for c in data["chains"]], [("demo", "running"), ("side", "launching")])
        self.assertEqual({r["name"]: r["session"] for r in data["resources"]}, {"simulator": s2, "emulator": "me"})
        self.assertEqual([(w["name"], w["state"], w["fix"], w["waits"]) for w in data["watches"]], [
            ("app!12", "pending", None, None), ("lib!10", "waiting-base", None, "lib!9"), ("lib!11", "pending", None, None),
            ("lib!9", "fixing", {"name": "side-fix-1", "state": "started"}, None)])
        self.assertEqual(json.loads(self.cli("status", "--json", at=t).stdout), data)
        fixture = json.loads(FIXTURE.read_text().split("\nexport default ", 1)[1])
        self.assertEqual(fixture["version"], capacity.STATUS_VERSION)
        self.assertEqual(shape(fixture), shape(data))  # change the fixture with the schema, and the board with it
        self.assertEqual([e["event"] for e in self.log_events()], ["take", "hold"])
        self.agents([], "fail")
        r = self.cli("status", at=t)
        self.assertTrue(r.stdout.startswith("slots ?/3 · claude agents did not answer"))
        data = json.loads((self.claude / "capacity" / "status.json").read_text())
        self.assertEqual((data["agents"], data["used"], data["chains"]), ("unknown", None, []))
        self.assertEqual(len(data["resources"]), 2)

    def test_a_watch_that_opens_or_goes_rewrites_its_part_of_status_json(self):
        path = self.claude / "capacity" / "status.json"
        t = time.time()
        arm = self.arm("app!12", "demo", t)
        capacity.write_watches(t)  # no snapshot yet: a whole one
        data = json.loads(path.read_text())
        self.assertEqual((data["version"], data["used"], [w["name"] for w in data["watches"]]), (3, 0, ["app!12"]))
        path.write_text(json.dumps({**data, "used": 2, "at": t - 900}))
        arm.unlink()
        self.agents([], "hang")
        capacity.write_watches()
        self.assertEqual(json.loads(path.read_text()), {**data, "used": 2, "at": t - 900, "watches": []},
                         "the rest stays as it was, and `claude agents` is not asked")
        self.agents([])
        path.write_text(json.dumps({**data, "version": 2}))
        capacity.write_watches(t)
        self.assertEqual(json.loads(path.read_text())["version"], 3, "an older snapshot is written anew")

    def test_a_watching_name_belongs_to_its_story(self):
        self.story("demo", "## demo-s2 · 2026-11-17 15:20\nSession: " + "a" * 8 + "-5555-5555-5555-" + "5" * 12 +
                   "\nNext: watching app!12 (https://gitlab.com/g/p/-/merge_requests/12)\n")
        self.assertEqual(capacity.stories()[0], {"demo-s2": "demo", "app!12": "demo"})

    def sleeper(self):
        """A live pid that is not this process: a launch's flowkit-spawn stand-in."""
        proc = subprocess.Popen(["sleep", "60"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        return proc.pid

    def prompt(self, header):
        path = self.tmp / f"p{len(list(self.tmp.glob('p*.md')))}.md"
        path.write_text(header + "\n\nToday: the slice.\nNeeds: not-in-the-header\n")
        return str(path)

    def queue(self):
        return [(e["name"], e["why"]) for e in capacity.entries()]

    def test_the_prompt_header(self):
        self.assertEqual(capacity.prompt_header(self.prompt("Repo: /r   Session: s2\nNeeds: simulator, backend\nLeft: 2")),
                         ({"simulator", "backend"}, 2))
        self.assertEqual(capacity.prompt_header(self.prompt("Repo: /r\nModel: opus   Needs: emulator   Left: 0")),
                         ({"emulator"}, 0))
        self.assertEqual(capacity.prompt_header(self.prompt("Repo: /r\nNeeds: none")), (set(), None))
        self.assertEqual(capacity.prompt_header(self.prompt("Repo: /r")), (set(), None), "the body is not the header")
        self.assertEqual(capacity.prompt_header(self.tmp / "missing.md"), (set(), None))

    def full_machine(self):
        """slots = 1, taken by a live chain `other` in its own repo; app declares the devices."""
        app = self.devices()
        self.conf.write_text("slots = 1\n" + self.conf.read_text())
        other = self.repo("other")
        sid = "0" * 8 + "-1111-1111-1111-" + "1" * 12
        self.story("other", "> launched other-s1\n")
        rows = [self.row("other-s1", sid, other, pid=os.getpid())]
        self.agents(rows)
        return app, rows, sid

    def test_a_full_machine_queues_a_new_chain(self):
        app, rows, _ = self.full_machine()
        prompt = self.prompt("Repo: /r\nNeeds: simulator\nLeft: 1")
        code, msg = capacity.gate(rows, "4242", app, "demo", "demo-s1", prompt, ["--name", "demo-s1", prompt], {})
        self.assertEqual(code, capacity.QUEUED)
        self.assertRegex(msg, r"^QUEUED #1, ~\d\d:\d\d · id=1 · slots 1/1$")
        (entry,) = capacity.entries()
        self.assertEqual({k: entry[k] for k in ("name", "story", "tree", "prompt", "argv", "needs", "left", "continuation", "by")},
                         {"name": "demo-s1", "story": "demo", "tree": str(app), "prompt": prompt,
                          "argv": ["--name", "demo-s1", prompt], "needs": ["simulator"], "left": 1,
                          "continuation": False, "by": "user"})
        self.assertEqual((self.held(), capacity.markers()), ({}, []), "a queued launch holds nothing")
        code, msg = capacity.gate(rows, "4243", self.repo("lib"), "side", "side-s1", "", [], {})
        self.assertEqual((code, self.queue()[1]), (capacity.QUEUED, ("side-s1", "slots 1/1")))
        self.assertIn("QUEUED #2,", msg)
        code, msg = capacity.gate(rows, "4244", app, "demo", "demo-s1", prompt, [], {}, dry=True)
        self.assertEqual(code, 0)
        self.assertIn("QUEUED #3, ", msg)
        self.assertIn("(dry run: nothing queued)", msg)
        self.assertEqual(len(capacity.entries()), 2, "a dry run queues nothing")
        self.assertEqual([e["event"] for e in self.log_events()], ["queue", "queue"])

    def test_over_cap_launches_anyway(self):
        app, rows, _ = self.full_machine()
        pid = str(self.sleeper())
        code, msg = capacity.gate(rows, pid, app, "demo", "demo-s1", self.prompt("Needs: simulator"), [], {}, over_cap=True,
                                  held=90)
        self.assertEqual((code, msg), (0, "new chain"))
        self.assertEqual(self.held(), {"simulator": f"launch:{pid}"})
        self.assertEqual([m["pid"] for m in capacity.markers()], [pid])
        self.assertEqual([e["event"] for e in self.log_events()], ["over-cap", "take", "launch"])
        self.assertEqual(self.log_events()[-1]["held"], 90, "the load-hold seconds, not the leases")

    def test_a_continuation_never_waits_for_a_slot(self):
        app, rows, _ = self.full_machine()
        self.story("demo", "> launched demo-s1\n")
        me = {"sessionId": "d" * 8 + "-1111-1111-1111-" + "1" * 12, "name": "demo-s1", "cwd": str(app)}
        rows.append(self.row("demo-s1", me["sessionId"], app))
        self.agents(rows)
        self.assertEqual(capacity.gate(rows, str(self.sleeper()), app, "demo", "demo-s2", "", [], me), (0, "continuation"))
        # while `claude agents` is unknown no new chain starts, but a chain goes on
        self.assertEqual(capacity.gate(None, "4250", self.repo("lib"), "side", "side-s1", "", [], {})[0], capacity.QUEUED)
        self.assertEqual(self.queue(), [("side-s1", "claude agents did not answer")])
        self.assertEqual(capacity.gate(None, str(self.sleeper()), app, "demo", "demo-s2", "", [], me), (0, "continuation"))
        # a first slice started by hand, in the tree it launches into, is not in any story file yet
        hand = {"sessionId": "e" * 8 + "-1111-1111-1111-" + "1" * 12, "name": "app-main", "cwd": str(app)}
        self.assertTrue(capacity.continues(hand, "demo", str(app)))
        self.assertFalse(capacity.continues({**hand, "name": "demo-coord"}, "demo", str(app)), "a coordinator starts chains")

    def test_two_closers_crossing_resources_never_deadlock(self):
        app = self.devices()
        self.conf.write_text(f"[{app}]\nresource simulator = s\nresource backend = b\n")
        a, b = "a" * 8 + "-1111-1111-1111-" + "1" * 12, "b" * 8 + "-1111-1111-1111-" + "1" * 12
        self.story("sa", "> launched sa-s1\n")
        self.story("sb", "> launched sb-s1\n")
        tree_a, tree_b = self.tmp / "wt-a", self.tmp / "wt-b"
        git("-C", str(app), "worktree", "add", "-q", str(tree_a), "-b", "a")
        git("-C", str(app), "worktree", "add", "-q", str(tree_b), "-b", "b")
        rows = [self.row("sa-s1", a, tree_a), self.row("sb-s1", b, tree_b)]
        self.agents(rows)
        self.assertEqual(capacity.take(rows, a, {"simulator"})[0], 0)
        self.assertEqual(capacity.take(rows, b, {"backend"})[0], 0)
        me_a = {"sessionId": a, "name": "sa-s1", "cwd": str(tree_a)}
        me_b = {"sessionId": b, "name": "sb-s1", "cwd": str(tree_b)}
        both = self.prompt("Needs: simulator, backend")
        code, msg = capacity.gate(rows, "5001", tree_a, "sa", "sa-s2", both, ["a"], me_a)
        self.assertEqual(code, capacity.QUEUED)
        self.assertIn("busy: backend is held by sb-s1", msg)
        self.assertEqual(self.held(), {"backend": b}, "the closer gave back what it held: no hold-and-wait")
        code, msg = capacity.gate(rows, "5002", tree_b, "sb", "sb-s2", both, ["b"], me_b)
        self.assertEqual(code, capacity.QUEUED)
        self.assertIn("simulator wanted by an earlier launch", msg)
        self.assertEqual(self.held(), {}, "nobody holds anything while waiting")
        pids = iter([self.sleeper(), self.sleeper()])
        with mock.patch.object(capacity, "spawn", side_effect=lambda e: next(pids)):
            first = capacity.drain(rows)
            self.assertEqual([e["name"] for e in first], ["sa-s2"])
            self.assertEqual(set(self.held().values()), {f"launch:{first[0]['pid']}"})
            self.assertEqual(capacity.drain(rows), [], "the second waits for both")
            capacity.launched(rows, first[0]["pid"])  # its launch gave back (or its session closed)
            self.assertEqual([e["name"] for e in capacity.drain(rows)], ["sb-s2"])
        self.assertEqual(capacity.entries(), [])

    def test_drain_is_first_in_first_out(self):
        app, rows, sid = self.full_machine()
        for n in (1, 2):
            capacity.gate(rows, str(4300 + n), self.repo(f"r{n}"), f"q{n}", f"q{n}-s1", "", [f"q{n}"], {})
        launched = []
        with mock.patch.object(capacity, "spawn", side_effect=lambda e: launched.append(e["name"]) or self.sleeper()):
            self.assertEqual(capacity.drain(rows), [])
            (self.claude / "load-hold").write_text("free memory 9%\n")
            self.assertEqual(capacity.drain(rows, ending={sid}), [], "a load hold stops the drain")
            (self.claude / "load-hold").unlink()
            started = capacity.drain(rows, ending={sid})  # other-s1's close is being recorded
        self.assertEqual(launched, ["q1-s1"], "one slot: the oldest, and the next one waits")
        self.assertEqual([c["state"] for c in capacity.chains(rows)], ["running", "launching"])
        self.assertEqual(self.queue(), [("q2-s1", "slots 1/1")])
        self.assertEqual(started[0]["argv"], ["q1"])
        self.assertEqual([e["event"] for e in self.log_events()][-1], "dequeue")
        self.assertLessEqual(self.log_events()[-1]["gap"], self.log_events()[-1]["waited"], "idle-with-queue's bound")
        status = json.loads((self.claude / "capacity" / "status.json").read_text())
        self.assertEqual([q["name"] for q in status["queue"]], ["q2-s1"])

    def test_spawn_runs_the_entry_detached(self):
        self.assertTrue(capacity.queued() is False)
        entry = {"id": 3, "tree": str(self.tmp), "argv": ["--help"]}
        pid = capacity.spawn(entry)
        deadline = time.time() + 10
        out = self.claude / "capacity" / "launched" / "3.log"
        while time.time() < deadline and capacity.pid_alive(pid):
            time.sleep(0.05)
        self.assertIn("Usage: flowkit-spawn", out.read_text())

    def one_checkout(self):
        """app has one checkout (`checkouts = 1`) and a device; demo-s1 is a live slice of story demo in it."""
        app = self.repo("app")
        self.conf.write_text(f"[{app}]\ncheckouts = 1\nresource simulator = a device check\n")
        sid = "d" * 8 + "-1111-1111-1111-" + "1" * 12
        self.story("demo", "> launched demo-s1\n")
        rows = [self.row("demo-s1", sid, app, pid=os.getpid())]
        self.agents(rows)
        return app, rows, {"sessionId": sid, "name": "demo-s1", "cwd": str(app)}

    def test_one_checkout_is_a_resource_every_launch_into_it_takes(self):
        app, rows, me = self.one_checkout()
        cfg = capacity.load_config(self.conf)
        self.assertEqual((capacity.checkout(cfg, app), capacity.repo_section(cfg, app)["resources"]["app-checkout"]),
                         ("app-checkout", "the repository's one checkout"))
        tree = self.tmp / "wt"
        git("-C", str(app), "worktree", "add", "-q", str(tree), "-b", "side")
        self.assertIsNone(capacity.checkout(cfg, tree), "a worktree is not the one checkout")
        self.assertIsNone(capacity.checkout(cfg, self.repo("lib")))
        pid = str(self.sleeper())
        self.assertEqual(capacity.gate([], pid, app, "demo", "demo-s1", "", [], {}), (0, "new chain"))
        self.assertEqual(self.held(), {"app-checkout": f"launch:{pid}"}, "no prompt names it")
        code, msg = capacity.gate([], "4301", app, "side", "side-s1", "", [], {})
        self.assertEqual(code, capacity.QUEUED)
        self.assertIn("busy: app-checkout is held by demo-s1", msg)
        capacity.launched(rows, pid, "demo-s1")
        self.assertEqual(capacity.take(rows, me["sessionId"], {"simulator"}), (0, "took simulator"),
                         "the checkout its launch took for it is no hold-and-wait")

    def test_a_watchs_launch_takes_its_storys_slot_back_and_is_nobodys_next_slice(self):
        app, rows, sid = self.full_machine()
        pid = str(self.sleeper())
        self.assertEqual(capacity.gate(rows, pid, app, "demo", "demo-fix-1", self.prompt("Needs: none\nLeft: 0"), [], {},
                                       watch="app!12"), (0, "watch launch"))
        events = self.log_events()
        self.assertEqual([e["event"] for e in events], ["over-cap", "launch"])
        self.assertEqual((events[1]["by"], events[1]["watch"], events[1]["kind"]), ("watch", "app!12", "watch launch"))
        # the poller's children carry no session, and one that did would still not be that session's next slice
        other = rows[0]["cwd"]
        (self.claude / "sessions").mkdir()
        (self.claude / "sessions" / "777.json").write_text(json.dumps({"sessionId": sid, "name": "other-s1", "cwd": other}))
        gate = ("gate", "--pid", "4302", "--tree", other, "--story", "other", "--name", "other-fix-1", "--dry-run")
        with mock.patch.dict(os.environ, {"CLAUDE_PID": "777"}):
            self.assertEqual(self.cli(*gate).stdout, "continuation\n")
            self.assertEqual(self.cli(*gate, "--from-watch", "other!3").stdout, "watch launch\n")

    def test_a_watchs_fix_goes_before_the_chains_next_slice_in_one_checkout(self):
        app, rows, me = self.one_checkout()
        self.assertEqual(capacity.take(rows, me["sessionId"], {"app-checkout"})[0], 0)  # as its launch handed it over
        self.assertEqual(capacity.gate(rows, "4400", app, "side", "side-s1", "", ["side"], {})[0], capacity.QUEUED)
        code, msg = capacity.gate(rows, "4401", app, "demo", "demo-fix-1", "", ["fix"], {}, watch="app!12")
        self.assertEqual(code, capacity.QUEUED)
        self.assertIn("busy: app-checkout is held by demo-s1", msg)
        self.assertEqual(capacity.entries()[1]["watch"], "app!12")
        code, msg = capacity.gate(rows, "4402", app, "demo", "demo-s2", "", ["next"], me)  # the slice closes with spawn
        self.assertEqual(code, capacity.QUEUED)
        self.assertIn("app-checkout goes to a watch's launch first", msg)
        self.assertEqual(self.held(), {}, "no hand-over past the fix: the closer gave the checkout back")
        launched = []
        with mock.patch.object(capacity, "spawn", side_effect=lambda e: launched.append(e["name"]) or self.sleeper()):
            capacity.drain(rows, ending={me["sessionId"]})
            self.assertEqual(launched, ["demo-fix-1"], "before the older launch and the chain's next slice")
            self.assertEqual([name for name, _ in self.queue()], ["side-s1", "demo-s2"])
            self.assertEqual(capacity.drain(rows, ending={me["sessionId"]}), [], "one works in the checkout at a time")

    def test_an_empty_queue_drains_in_one_read(self):
        self.agents([], "hang")
        start = time.monotonic()
        r = self.cli("drain")
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))
        self.assertLess(time.monotonic() - start, 0.9, "no `claude agents` call")
        self.assertFalse((self.claude / "capacity" / "status.json").exists())

    def test_launched_hands_over_or_gives_back(self):
        app = self.devices()
        new = "c" * 8 + "-1111-1111-1111-" + "1" * 12
        rows = [self.row("demo-s2", new, app)]
        for pid, name, want in ((self.sleeper(), "demo-s2", {"simulator": new}), (self.sleeper(), None, {})):
            for f in (self.claude / "capacity" / "resources").glob("*.json"):
                f.unlink()
            with capacity.lock():
                capacity.reserve(pid, str(app), "demo", "demo-s2", {"simulator"}, time.time())
            capacity.launched(rows, pid, name)
            self.assertEqual((self.held(), capacity.markers()), (want, []))
        self.assertEqual([e["event"] for e in self.log_events()], ["take", "repoint", "take", "release"])

    def test_the_wrapper_runs_status_with_nothing_configured(self):
        r = subprocess.run([str(PLUGIN / "bin" / "flowkit-capacity"), "status"], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "slots 0/3\n", ""))
        self.assertTrue((self.claude / "capacity" / "status.json").exists())

    # waves: the scheduler over the queue and the running chains

    T = 1_800_000_000.0  # a fixed clock for the scheduler fixtures
    M = 60

    def running(self, tree, sid="0" * 8 + "-2222-2222-2222-" + "2" * 12):
        return {"tree": str(tree), "repo": str(tree), "state": "running", "sessions": [{"session": sid, "name": "x"}]}

    def plan(self, found, items, log, slots=3, waiting=()):
        cfg = {**capacity.DEFAULTS, "slots": slots, "repos": {}}
        out = capacity.forecast(cfg, found, list(waiting), items, self.T, log)
        return [(round((p["start"] - self.T) / self.M), round((p["ready"] - self.T) / self.M), p["wave"])
                if p["start"] is not None else None for _, p in sorted(out.items(), key=lambda kv: (kv[0][0] != "queue", kv[0][1]))]

    def item(self, tree, slices=1, needs=()):
        return {"name": Path(tree).name, "tree": str(tree), "slices": slices, "needs": set(needs)}

    def test_waves_three_slots_foreign_chains_and_fallback_minutes(self):
        f1, f2, a, b, c = (self.repo(n) for n in ("f1", "f2", "a", "b", "c"))
        log = [  # two foreign chains: f1 started 10 min ago with 2 slices left after it, f2 40 min ago, its last
            {"t": self.T - 10 * self.M, "event": "launch", "name": "f1-s1", "story": "f1", "tree": str(f1), "left": 2},
            {"t": self.T - 40 * self.M, "event": "launch", "name": "f2-s3", "story": "f2", "tree": str(f2), "left": 0}]
        # no spans in the log: every repo is planned at slice_minutes (50)
        self.assertEqual(self.plan([self.running(f1), self.running(f2)],
                                   [self.item(a), self.item(b, 2), self.item(c)], log), [
            (0, 50, 1),    # a: the third slot, now
            (10, 110, 2),  # b: f2's last slice ends at its 50 minutes
            (50, 100, 3)]) # c: a ends; f1 still has 140 minutes to go
        # a slice past its median gets ten minutes yet, and a chain with no launch in the log one slice from now
        log[1]["t"] = self.T - 90 * self.M
        self.assertEqual(self.plan([self.running(f2)], [self.item(a)], log, slots=1), [(10, 60, 2)])
        self.assertEqual(self.plan([self.running(f2)], [self.item(a)], [], slots=1), [(50, 100, 2)])

    def test_waves_a_shared_resource_and_the_users_hold(self):
        a, b, c, d = (self.repo(n) for n in "abcd")
        self.devices()
        capacity.hold(None, {"emulator"}, 30, t=self.T)
        self.assertEqual(self.plan([], [self.item(a, 2, ["simulator"]), self.item(b, 1, ["simulator"]), self.item(c),
                                        self.item(d, 1, ["emulator"])], [], slots=4), [
            (0, 100, 1),    # a takes the simulator
            (100, 150, 3),  # b waits for it
            (0, 50, 1),     # c needs nothing: it passes b, which waits for a resource only
            (30, 80, 2)])   # d: the emulator is the user's for 30 minutes
        # a running chain holds its lease to its end; a queued launch goes before the batch
        sid = "e" * 8 + "-2222-2222-2222-" + "2" * 12
        capacity.take([self.row("x", sid, a)], sid, {"simulator"}, t=self.T)
        queued = [{"id": 4, "tree": str(c), "needs": ["simulator"], "left": 1, "continuation": False}]
        self.assertEqual(self.plan([self.running(a, sid)], [self.item(b, 1, ["simulator"])], [], waiting=queued), [
            (50, 150, 2),    # the queued launch, two slices, once the chain in a lets the simulator go
            (150, 200, 3)])  # the batch behind it

    def test_waves_medians_leave_out_held_time_and_parked_chains(self):
        r = self.repo("r")
        ev = lambda m, event, **kw: {"t": self.T + m * self.M, "event": event, "story": "r", "tree": str(r), **kw}
        log = [ev(0, "launch", name="r-s1", by="user"),
               ev(20, "launch", name="r-s2", by="chain", launcher="r-s1"),            # 20
               ev(50, "launch", name="r-s3", by="chain", launcher="r-s2"),            # 30
               ev(100, "queue", id=7, name="r-s4", by="chain", launcher="r-s3", held=600),  # 50 less 10 held
               {"t": self.T + 130 * self.M, "event": "dequeue", "id": 7, "name": "r-s4", "story": "r"},
               ev(130 + 13 * 60, "launch", name="r-s5", by="chain", launcher="r-s4")]  # parked: left out
        self.assertEqual(capacity.medians(log), {str(r): 30 * self.M})
        self.assertEqual(capacity.medians(log[:3]), {}, "two spans: slice_minutes stands in")

    def test_the_gate_queues_with_the_forecast_eta(self):
        app, rows, _ = self.full_machine()
        code, msg = capacity.gate(rows, "4242", app, "demo", "demo-s1", self.prompt("Left: 1"), [], {}, t=self.T)
        self.assertEqual(code, capacity.QUEUED)
        self.assertEqual(capacity.entries()[0]["eta"], self.T + 50 * self.M, "other-s1's one slice at slice_minutes")
        self.assertEqual(self.log_events()[-1]["left"], 1)

    def test_waves_plan_line_and_the_test_queue(self):
        app = self.devices()
        a, b, c = (self.repo(n) for n in "abc")
        self.story("epic", "> launched app-s1\n")
        rows = [self.row("app-s1", "f" * 8 + "-2222-2222-2222-" + "2" * 12, app)]
        self.agents(rows)
        (self.claude / "stories" / "epic.tests.md").write_text(
            "# epic test queue\nLogin · !1 · sim · ready to test ·  · on the release build\nOld · !2 · sim · tested · \n")
        lines = capacity.waves(rows, [f"a={a}:2:simulator", f"b={b}", f"c={c}:1:emulator+simulator"], "epic",
                               write=True, t=self.T)
        hm = lambda m: time.strftime("%H:%M", time.localtime(self.T + m * self.M))
        self.assertEqual(lines, [
            f"a · wave 1 · now · ready ~{hm(100)} · 2 slices · needs simulator",
            f"b · wave 1 · now · ready ~{hm(50)} · 1 slice · shed",
            f"c · wave 2 · start ~{hm(100)} · ready ~{hm(150)} · 1 slice · needs emulator, simulator",
            f"Plan: 1 of 3 now (a) · 1 queued (c ~{hm(100)}) · 1 shed (b) · slots 3/3 · 1 wait on your testing"])
        self.assertEqual(capacity.read_tests("epic")[1][-1], ["c", "", "", "queued, wave 2, needs emulator, simulator", hm(150)])
        self.assertEqual(capacity.ties(), {"c": {"story": "epic", "item": "c"}})
        self.assertIn(("waves", 3), [(e["event"], e.get("items")) for e in self.log_events()], "a batch, for the report")
        # the drain moves the line to running with its ready time
        capacity.write_json(self.claude / "capacity" / "queue" / "1.json",
                            {"name": "c", "story": "epic", "tree": str(c), "argv": [], "needs": ["simulator"], "left": 0,
                             "continuation": False, "queued": self.T})
        with mock.patch.object(capacity, "spawn", side_effect=lambda e: self.sleeper()):
            capacity.drain([], t=self.T + 5 * self.M)
        self.assertEqual(capacity.read_tests("epic")[1][-1][3:], ["running", hm(55)])
        self.assertEqual(capacity.read_tests("epic")[1][0], ["Login", "!1", "sim", "ready to test", "", "on the release build"],
                         "a sixth field is a note, not the ETA, and a rewrite keeps it")
        self.assertEqual(capacity.ties(), {})
        with self.assertRaises(ValueError):
            capacity.waves(rows, ["no-tree"], t=self.T)

    def test_testq_writes_through_the_lock(self):
        procs = [subprocess.Popen(["python3", str(CAPACITY), "testq", "epic", "set", f"{w}-{n}", "--state", "building",
                                   "--eta", "14:30"], stdout=subprocess.DEVNULL) for w in "ab" for n in range(8)]
        self.assertEqual([p.wait() for p in procs], [0] * 16)
        _, lines = capacity.read_tests("epic")
        self.assertEqual(sorted(x[0] for x in lines), sorted(f"{w}-{n}" for w in "ab" for n in range(8)), "no write lost")
        self.assertEqual({x[4] for x in lines}, {"14:30"})
        r = self.cli("testq", "epic", "set", "a-0", "--state", "ready to test", "--eta", "", "--mr", "!9")
        self.assertEqual(r.stdout.strip(), "a-0 · !9 ·  · ready to test ·")
        test = [e for e in self.log_events() if e["event"] == "test"]
        self.assertEqual(len(test), 17)
        self.assertEqual((test[-1]["before"], test[-1]["state"], test[-1]["eta"]), ("building", "ready to test", ""))
        for w in "ab":
            for n in range(8):
                self.assertEqual(self.cli("testq", "epic", "drop", f"{w}-{n}").returncode, 0)
        self.assertFalse(capacity.tests_path("epic").exists(), "an empty queue deletes the file")
        self.assertEqual(self.cli("testq", "epic", "drop", "a-0").returncode, 1)

    def test_testq_eta_is_the_fifth_field_and_the_notes_stay(self):
        notes = "sim build 412 · dark mode too · see the story · after the release · ask before merging"
        path = capacity.tests_path("epic")
        path.write_text(f"# epic test queue\nLogin · !1 · sim · building · 14:30 · {notes}\nShort · !2\nJust a sentence\n")
        r = self.cli("testq", "epic", "set", "Login", "--eta", "15:10", "--state", "ready to test")
        self.assertEqual(r.stdout.strip(), f"Login · !1 · sim · ready to test · 15:10 · {notes}")
        self.assertEqual(self.cli("testq", "epic", "set", "Login", "--eta", "").stdout.strip(),
                         f"Login · !1 · sim · ready to test ·  · {notes}", "a cleared ETA leaves its place, the notes stay sixth")
        self.assertEqual(self.cli("testq", "epic", "set", "Short", "--eta", "16:00").stdout.strip(), "Short · !2 ·  ·  · 16:00")
        self.assertEqual(path.read_text(), "# epic test queue\n"
                         f"Login · !1 · sim · ready to test ·  · {notes}\nShort · !2 ·  ·  · 16:00\nJust a sentence\n")
        self.assertEqual(self.cli("testq", "epic").stdout.splitlines()[0], f"Login · !1 · sim · ready to test ·  · {notes}")

    def test_limit_runs_a_repo_slots_fewer_for_a_while(self):
        app, lib = self.devices(), self.repo("lib")
        self.conf.write_text(self.conf.read_text() + f"[{lib}]\nslots = 1\n")
        t = time.time()
        self.assertEqual(self.cli("limit", str(app), "-1", "--hours", "2", at=t).stdout.strip(),
                         f"repo {app}: 2 slots until {time.strftime('%H:%M', time.localtime(t + 7200))}")
        self.cli("limit", str(lib), "-1", at=t)
        cfg = capacity.load_config()
        self.assertEqual((cfg["repos"][str(app)]["slots"], cfg["repos"][str(lib)]["slots"]), (2, 1), "never under one")
        with mock.patch.dict(os.environ, {"FLOWKIT_CAPACITY_NOW": str(t + 7300)}):
            self.assertNotIn("slots", capacity.load_config()["repos"][str(app)], "run out")
        self.cli("limit", str(lib), "0", at=t)
        self.assertEqual(capacity.load_config()["repos"][str(lib)]["slots"], 1)
        self.assertEqual([(e["event"], e["delta"]) for e in self.log_events()], [("limit", -1), ("limit", -1), ("limit", 0)])

    def write_log(self, *events):
        path = self.claude / "capacity" / "log.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e) + "\n" for e in events))

    def write_transcript(self, sid, name, entries):
        """A transcript under projects/: an agent-name line, then (seconds, entry) pairs."""
        iso = lambda s: datetime.fromtimestamp(s, timezone.utc).isoformat().replace("+00:00", "Z")  # noqa: E731
        path = self.claude / "projects" / "-w-app" / f"{sid}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(e) + "\n" for e in [{"type": "agent-name", "agentName": name, "sessionId": sid}] + [
            {**e, "timestamp": iso(at), "sessionId": sid} for at, e in entries]))

    def test_report_over_a_synthetic_log(self):
        T = datetime(2026, 11, 10, 12, 0).timestamp()
        M = 60
        night = datetime(2026, 11, 10, 23, 0).timestamp()
        self.write_log(
            {"t": T, "event": "launch", "name": "a-s1", "held": 120},
            {"t": T + M, "event": "queue", "id": 1, "name": "b-s1", "needs": ["simulator"],
             "why": "busy: simulator is held by a-s1 since 12:00 (take)", "eta": T + 31 * M, "held": 0},
            {"t": T + 41 * M, "event": "dequeue", "id": 1, "name": "b-s1", "waited": 2400, "gap": 900},
            {"t": night - 90 * M, "event": "queue", "id": 2, "name": "c-s1", "needs": [], "why": "slots 3/3",
             "eta": night - 30 * M},
            {"t": night, "event": "dequeue", "id": 2, "name": "c-s1", "waited": 5400, "gap": 5400},
            {"t": T + 2 * M, "event": "reclaim", "resource": "emulator"},
            {"t": T + 3 * M, "event": "over-cap", "name": "d-s1"},
            {"t": T + 4 * M, "event": "audit", "resource": "simulator", "session": "s9", "holder": "a-s1",
             "command": "xcrun simctl install booted x"},
            {"t": T + 5 * M, "event": "note", "kind": "collision", "text": "two installs on the simulator"},
            *({"t": T + n, "event": "waves", "story": "epic", "items": n} for n in (4, 5, 6, 2)),
            {"t": T, "event": "test", "story": "epic", "item": "b", "op": "set", "before": None,
             "state": "queued, wave 2", "eta": T + 60 * M},
            {"t": T + 80 * M, "event": "test", "story": "epic", "item": "b", "op": "set", "before": "queued, wave 2",
             "state": "ready to test"},
            {"t": T - 2 * capacity.DAY, "event": "over-cap", "name": "before-the-window"},
            # a watch's own lines, and the gate's for the launches it made: in no count but `watched`
            {"t": T + 6 * M, "event": "watch", "op": "add", "name": "app!12", "story": "epic"},
            {"t": T + 30 * M, "event": "watch", "op": "red", "name": "app!12", "story": "epic"},
            {"t": T + 30 * M, "event": "watch", "op": "fix", "name": "app!12", "story": "epic", "fix": "epic-fix-1"},
            {"t": T + 30 * M, "event": "over-cap", "name": "epic-fix-1", "watch": "app!12"},
            {"t": T + 30 * M, "event": "queue", "id": 3, "name": "epic-fix-1", "needs": ["simulator"], "by": "watch",
             "why": "busy: simulator is held by a-s1 since 12:00 (take)", "eta": T + 35 * M, "held": 60, "watch": "app!12"},
            {"t": T + 50 * M, "event": "dequeue", "id": 3, "name": "epic-fix-1", "waited": 1200, "gap": 1200, "watch": "app!12"},
            {"t": T + 50 * M, "event": "launch", "name": "epic-fix-2", "held": 300, "by": "watch", "watch": "app!13"},
            {"t": T + 55 * M, "event": "watch", "op": "done", "name": "app!12", "story": "epic", "end": "armed", "minutes": 49},
            {"t": T + 95 * M, "event": "watch", "op": "green", "name": "app!12#2", "story": "epic", "minutes": 40},
            {"t": T + 95 * M, "event": "watch", "op": "done", "name": "app!12#2", "story": "epic", "end": "green", "minutes": 40},
            {"t": T - 2 * capacity.DAY, "event": "watch", "op": "done", "name": "app!3", "end": "green", "minutes": 500},
        )
        usage = lambda n: {"type": "assistant", "message": {"usage": {"input_tokens": n}}}  # noqa: E731
        typed = lambda text: {"type": "user", "origin": {"kind": "human"}, "message": {"content": text}}  # noqa: E731
        self.write_transcript("c" * 8, "epic-coord", [
            (T + 10 * M, typed("go on with the batch")),
            (T + 11 * M, usage(100_000)),
            (T + 20 * M, {"type": "user", "isMeta": True, "origin": {"kind": "peer"}, "message": {"content": "slice done"}}),
            (T + 21 * M, {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}),
            (T + 30 * M, typed("what's the status?")),
            (T + 60 * M, {"type": "system", "subtype": "compact_boundary",
                          "compactMetadata": {"preTokens": 150_000, "postTokens": 20_000}}),
            (T + 70 * M, usage(50_000)),
        ])
        self.write_transcript("d" * 8, "app-s1", [(T + 15 * M, typed("a worker's prompt")), (T + 40 * M, usage(10))])
        data = capacity.report(T, T + capacity.DAY)
        self.assertEqual(data["launch_holds"], {"count": 1, "minutes": 2})
        self.assertEqual(data["queued"], {"count": 2, "minutes": 130, "launch_eta_error": 20, "ready_eta_error": 20,
                                          "ready_items": 1})
        self.assertEqual(data["resource_waits"], {"simulator": {"count": 1, "minutes": 40}})
        self.assertEqual((data["reclaims"], data["over_cap"], data["batches"]), (1, 1, 3))
        self.assertEqual(data["idle_with_queue"], {"day": 15 + 30, "nights": {"2026-11-10": 60}})
        self.assertEqual(len(data["collisions"]), 2)
        self.assertEqual(data["watched"], {"count": 2, "minutes": 89, "fixes": 1})
        self.assertEqual((data["sessions"], data["over_slots_minutes"]), (2, 0))
        self.assertEqual(data["coordinators"], {"epic-coord": {"2026-11-10": {
            "typed": 2, "status": 1, "compactions": 1, "growth": 50_000 + 130_000}}})
        lines = capacity.report_lines(data)
        self.assertIn("coordinator     epic-coord: 11-10 180k, 1 compacted, 2 typed (1 status)", lines)
        self.assertEqual(lines[-2], "watched         89 min over 2 watches · 1 fix slice")
        self.assertEqual(lines[-1], "pass            batches enough · idle nights pass · ETA error pass · growth pass"
                                    " (180k epic-coord 11-10) · status asks no baseline given · collisions FAIL")
        base = capacity.report(T, T + capacity.DAY, baseline=True)
        self.assertNotIn("queued", base)
        self.assertEqual(capacity.report_lines(base)[-1], "typed prompts   2.0/day to coordinators, 1.0/day asking for status")
        against = {**base, "coordinators": {"x-coord": {"2026-11-03": {"typed": 9, "status": 4, "compactions": 0}}}}
        self.assertIn("status asks pass (1.0 vs 4.0/day)", capacity.report_lines(data, against)[-1])
        r = self.cli("report", "--since", "2026-11-10", "--until", "2026-11-12", "--json")
        self.assertEqual((r.returncode, json.loads(r.stdout)["over_cap"]), (0, 1), r.stderr)
        self.assertEqual(self.cli("report", "--since", "last week").returncode, 2)

    def test_the_audit_hook_logs_a_match_and_never_denies(self):
        app = self.repo("app")
        self.conf.write_text(f"[{app}]\nresource simulator = s\naudit simulator = xcrun simctl|maestro test\n"
                             "audit emulator = adb ( \nresource backend = b\n")
        mine, other = "a" * 8 + "-1111-1111-1111-" + "1" * 12, "b" * 8 + "-1111-1111-1111-" + "1" * 12
        call = lambda sid, cmd: capacity.audit({"session_id": sid, "cwd": str(app),  # noqa: E731
                                                "tool_input": {"command": cmd}}, t=100)
        self.assertEqual(call(mine, "git status"), [])
        self.assertEqual(call(mine, "xcrun simctl install booted App.app"), ["simulator"], "free, not taken: logged")
        rows = [self.row("app-s1", mine, app)]
        self.agents(rows)
        self.assertEqual(capacity.take(rows, mine, {"simulator"})[0], 0)
        self.assertEqual(call(mine, "maestro test flow.yaml"), [], "the holder runs it")
        self.assertEqual(call(other, "maestro test flow.yaml"), ["simulator"])
        audits = [e for e in self.log_events() if e["event"] == "audit"]
        self.assertEqual([(e["session"], e["holder"]) for e in audits], [(mine, None), (other, "app-s1")])
        for stdin in ('{"session_id": "x", "tool_input": {"command": "xcrun simctl boot"}}', "not json", ""):
            r = subprocess.run(["python3", str(CAPACITY), "audit"], input=stdin, capture_output=True, text=True)
            self.assertEqual((r.returncode, r.stdout), (0, ""), "never denies, never answers the harness")
        self.assertEqual(len([e for e in self.log_events() if e["event"] == "audit"]), 3)
        self.assertEqual(self.cli("note", "collision", "two installs").stdout.strip(), "noted collision: two installs")
        self.assertEqual(self.log_events()[-1], {"t": self.log_events()[-1]["t"], "event": "note", "kind": "collision",
                                                 "text": "two installs"})


if __name__ == "__main__":
    unittest.main()
