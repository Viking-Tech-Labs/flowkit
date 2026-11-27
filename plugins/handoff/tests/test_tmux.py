"""Run with `python3 -m unittest discover plugins/handoff/tests`. Loads flowkit-tmux as a module and
swaps the command runners of the backend.py it loads for a stub that serves panes, windows and
`claude agents`, and records the tmux writes, so nothing reaches a real tmux server or the user's
files."""
import contextlib
import importlib.machinery
import io
import importlib.util
import json
import os
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

BIN = Path(__file__).resolve().parent.parent / "bin" / "flowkit-tmux"
SEP = "\x1f"


def load(path=BIN):
    loader = importlib.machinery.SourceFileLoader("flowkit_tmux", str(path))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def backend(ft, tmp):
    """The backend.py a loaded flowkit-tmux reaches tmux and claude through, its file under tmp."""
    b = ft.backend()
    b.STATE = tmp / "flowkit" / "sessions.json"
    b.CONF = tmp / "handoff.conf"  # never the user's driver
    return b


class Labels(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        for sub in ("stories", "sessions", "flowkit/retry", "projects/-repo"):
            (self.tmp / sub).mkdir(parents=True)
        self.ft = load()
        self.ft.CLAUDE = self.tmp
        self.ft.CONFIG = self.tmp  # never the user's handoff.conf
        self.b = backend(self.ft, self.tmp)
        self.b.tmux = lambda *cmd: types.SimpleNamespace(stdout=self.run_stub("tmux", *cmd), returncode=0)
        self.b.agents = lambda *cmd: json.loads(self.run_stub("claude", "agents", "--json"))
        self.b.parents = dict
        self.rows, self.pane_list, self.windows, self.writes = [], [], {}, []
        self.later, self.listed, self.asked = None, 0, 0  # a second pane list, served from the second list-panes on
        self.screens = {}  # pane id → what capture-pane shows

    def test_a_test_reads_its_own_handoff_conf(self):
        (self.tmp / "handoff.conf").write_text("remind=on\n")
        self.assertEqual(self.ft.setting("remind"), "on")

    def run_stub(self, *cmd):
        if cmd[:2] == ("claude", "agents"):
            self.asked += 1
            return json.dumps(self.rows)
        if cmd[:2] == ("tmux", "list-panes") and cmd[4].startswith("p" + SEP):  # what each pane and tab shows now
            return "".join(SEP.join(["p", p[1], p[8]]) + "\n" for p in self.pane_list) + \
                "".join(SEP.join(["w", wid, "", label]) + "\n" for wid, label in self.windows.items())
        if cmd[:2] == ("tmux", "list-panes"):  # a pane's row here keeps what it shows at 8, its rung before its born
            self.listed += 1
            panes = self.later if self.later is not None and self.listed > 1 else self.pane_list
            return "".join(SEP.join(p[i] for i in (0, 1, 2, 3, 4, 5, 6, 7, 9, 10, 11, 13, 12, 14)) + "\n" for p in panes)
        if cmd[:2] == ("tmux", "capture-pane"):
            return self.screens.get(cmd[-1], "")
        self.writes.append(cmd[1:])
        return ""

    def window(self, wid, story="", label="", hold=""):
        self.windows[wid] = label
        self.story = story
        self.wid = wid
        self.hold = hold

    def pane(self, pid, n, cmd, born="", parked=""):
        slug = "" if self.story == "adhoc" else self.story
        self.pane_list.append([pid, f"%{n}", "work", self.wid, "1", "w", str(n), cmd, "", self.story, slug, self.hold, "",
                               born, parked])

    def session(self, n, status, live=None, idle_for=0, closed=False, retry=False, touched=None, name=""):
        """A claude pane in the current window, its agents row and, optionally, its sessions file."""
        sid = f"{n:08d}-0000-0000-0000-000000000000"
        pid = str(1000 + n)
        self.pane(pid, n, "2.1.304")
        self.rows.append({"kind": "interactive", "pid": int(pid), "sessionId": sid, "status": status, "name": name,
                          "startedAt": (time.time() - 2 * 3600) * 1000})
        updated = (time.time() - idle_for) * 1000
        (self.tmp / "sessions" / f"{pid}.json").write_text(json.dumps(
            {"pid": int(pid), "sessionId": sid, "status": live or status, "statusUpdatedAt": updated}))
        transcript = self.tmp / "projects" / "-repo" / f"{sid}.jsonl"
        transcript.write_text("{}\n")
        old = time.time() - (idle_for if touched is None else touched)
        os.utime(transcript, (old, old))
        if closed:
            with (self.tmp / "stories" / f"{self.story or 'other'}.md").open("a") as f:
                f.write(f"\n## s{n} · 2026-10-20 12:00\nSession: {sid} · Repo: /repo\n")
        if retry:
            (self.tmp / "flowkit" / "retry" / f"{sid}.json").write_text(json.dumps(retry if isinstance(retry, dict) else {}))
        return sid

    def arm(self, name, story="st"):
        """An open watch of the story: its arm file, as scripts/watch.py keeps it until its section is in."""
        path = self.tmp / "flowkit" / "watches" / f"{name}.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({"name": name, "story": story, "state": "pending"}))
        return path

    def label(self, wid):
        self.writes.clear()
        self.ft.closed.cache_clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.ft.waiting()
        for args in self.writes:
            for i, arg in enumerate(args):
                if arg == "@flowkit_tab" and args[i - 1] == wid:
                    return None if args[i - 3] == "-u" else args[i + 1]
        return "unchanged"

    def one(self, **kw):
        self.setUp()
        self.window("@1", "st")
        self.session(1, **kw)
        return self.label("@1")

    def test_each_state(self):
        self.assertEqual(self.one(status="waiting"), "needs you")
        self.assertEqual(self.one(status="busy", retry=True), "error")
        self.assertEqual(self.one(status="idle", retry={"kind": "server_error"}), "error")
        self.assertEqual(self.one(status="idle", retry={"kind": "rate_limit"}), "limit")
        self.assertEqual(self.one(status="busy"), "running")
        self.assertEqual(self.one(status="busy", live="shell"), "running")
        self.assertEqual(self.one(status="busy", live="shell", closed=True), "watching")
        self.assertEqual(self.one(status="idle", idle_for=16 * 60), "stalled")
        self.assertEqual(self.one(status="idle", closed=True), "done")
        self.assertEqual(self.one(status="idle", idle_for=60), "unchanged")
        self.assertEqual(self.one(status="idle", idle_for=16 * 60, name="st-report"), "unchanged")

    def test_not_stalled_while_the_transcript_or_a_fork_moves(self):
        self.assertEqual(self.one(status="idle", idle_for=16 * 60, touched=60), "unchanged")
        self.setUp()
        self.window("@1", "st")
        sid = self.session(1, status="idle", idle_for=16 * 60)
        forks = self.tmp / "projects" / "-repo" / sid / "subagents"
        forks.mkdir(parents=True)
        (forks / "agent-a.jsonl").write_text("{}\n")
        self.assertEqual(self.label("@1"), "unchanged")

    def test_a_reused_pid_is_not_this_session(self):
        self.setUp()
        self.window("@1", "st")
        self.session(1, status="busy", live="shell", closed=True)
        path = self.tmp / "sessions" / "1001.json"
        path.write_text(json.dumps({**json.loads(path.read_text()), "sessionId": "someone-else"}))
        self.assertEqual(self.label("@1"), "running")

    def test_a_close_in_another_story_counts_only_on_the_adhoc_tab(self):
        self.window("@1", "st")
        self.story = "other"
        self.session(1, status="idle", closed=True)
        self.story = "st"
        self.pane_list[-1][9] = "st"
        self.assertEqual(self.label("@1"), "unchanged")
        self.pane_list[-1][9] = "adhoc"
        self.assertEqual(self.label("@1"), "done")

    def test_most_urgent_wins_and_workspace_windows_stay_bare(self):
        self.window("@1", "st", label="done")
        self.session(1, status="idle", closed=True)
        self.session(2, status="busy", live="shell", closed=True)
        self.session(3, status="busy")
        self.session(4, status="idle", idle_for=20 * 60)
        self.assertEqual(self.label("@1"), "stalled")
        self.session(8, status="idle", retry={"kind": "rate_limit"})
        self.assertEqual(self.label("@1"), "limit")
        self.session(5, status="busy", retry=True)
        self.assertEqual(self.label("@1"), "error")
        self.session(6, status="waiting")
        self.assertEqual(self.label("@1"), "needs you")
        self.window("@2")
        self.session(7, status="waiting")
        self.assertEqual(self.label("@2"), "unchanged")

    def test_a_story_with_an_open_watch_is_watching_while_nothing_is_more_urgent(self):
        self.window("@1", "st", label="done")
        self.session(1, status="idle", idle_for=60, name="st-report")
        self.session(2, status="idle", closed=True)
        self.window("@2", "other")
        self.session(3, status="idle", closed=True)
        self.assertEqual((self.label("@1"), self.label("@2")), ("unchanged", "done"))
        arm = self.arm("app!7")
        (arm.parent / ".app!7.json.123").write_text("{")  # a write caught halfway
        self.assertEqual((self.label("@1"), self.label("@2")), ("watching", "done"))
        self.session(4, status="busy")
        self.assertEqual(self.label("@2"), "running")
        self.wid, self.story = "@1", "st"
        self.session(5, status="idle", idle_for=20 * 60)
        self.assertEqual(self.label("@1"), "stalled")
        self.rows.pop(), self.pane_list.pop()
        arm.unlink()
        self.assertEqual(self.label("@1"), "unchanged")  # back to done, which the tab still says

    def test_a_label_goes_when_its_sessions_do(self):
        self.window("@1", "st", label="running")
        self.assertIsNone(self.label("@1"))


# Claude Code's folder-trust prompt as a pane shows it: a rule row, the text, the options, the key hints.
TRUST = "\n" + "─" * 120 + """
 Accessing workspace:

 /Users/me/repo

 Quick safety check: Is this a project you created or one you trust? If not, take a moment to
 review what's in this folder first.

 Claude Code'll be able to read, edit, and execute files here.

 ❯ No, exit
   Yes, I trust this folder

 Enter to confirm · Esc to cancel
""" + "\n" * 20
CHROME = """  Claude will use your Chrome browser by default — navigating sites, filling forms, and capturing screenshots.
  ❯ No, keep browser tools off
    Yes, use my browser
  Enter to confirm · Esc to keep browser tools off
"""
SESSIONS = """ 3 sessions

 ❯ demo-s1   working   2m
   demo-s2   idle      1h
"""
WORKING = """⏺ Reading the plan.

✻ Working… (12s · esc to interrupt)

""" + "─" * 120 + "\n❯ \n" + "─" * 120 + "\n  ⏵⏵ auto mode on\n"


class Outside(unittest.TestCase):
    """The marks with an outside driver named in handoff.conf: its places in place of tmux's panes."""

    def test_the_marks_go_to_the_driver_the_conf_names(self):
        tmp = Path(tempfile.mkdtemp())
        for sub in ("stories", "sessions", "flowkit", "bin"):
            (tmp / sub).mkdir()
        stub = tmp / "bin" / "flowkit-driver-stub"
        stub.write_text(f"#!/bin/sh\necho \"driver $*\" >> {tmp}/calls\ncase \"$1\" in\n"
                        "list) printf 'p1\\t500\\np2\\t\\n';;\nscreen) echo nothing;;\nlabel) ;;\n*) exit 2;;\nesac\n")
        stub.chmod(0o755)
        ft = load()
        ft.CLAUDE = ft.CONFIG = tmp
        b = backend(ft, tmp)
        b.CONF.write_text("driver=stub\n")
        sid = "00000001-0000-0000-0000-000000000000"
        b.agents = lambda *cmd: [{"kind": "interactive", "pid": 700, "sessionId": sid, "status": "waiting", "name": "st-s1",
                                  "startedAt": time.time() * 1000}]
        b.parents = lambda: {700: 600, 600: 500}
        b.tmux = lambda *cmd: self.fail(f"tmux {cmd}")
        said = io.StringIO()
        with mock.patch.dict(os.environ, {"PATH": f"{tmp / 'bin'}{os.pathsep}{os.defpath}"}):
            self.assertIs(ft.terminal(), b.driver("stub"))
            b.register(sid, "stub", "p1", "st")
            with contextlib.redirect_stdout(said):
                ft.waiting()
        calls = (tmp / "calls").read_text().splitlines()
        self.assertEqual([c for c in calls if c.startswith("driver label")], ["driver label p1 waiting", "driver label p2 "])
        self.assertIn("driver screen p2", calls, "a place with no session listed may sit at a start-up prompt")
        self.assertNotIn("driver screen p1", calls)
        self.assertIn("1 needs you", said.getvalue())


class Parked(Labels):
    """A claude pane no session is listed for, its screen ending on a dialog for a minute: a start-up
    prompt nobody answered. The pane waits on the user, its tab says `needs you`, and
    flowkit/parked.json lists it while it does."""

    def setUp(self):
        super().setUp()
        self.window("@1", "st")
        self.now = int(time.time())
        self.file = self.tmp / "flowkit" / "parked.json"

    def seen(self, at=None):
        """When pane %1's dialog was first seen, as the backend's file keeps it; `at` sets it."""
        if at:
            self.b.keep(lambda doc: doc["places"].update({"tmux %1": {"parked": at}}))
        return self.b.kept()["places"].get("tmux %1", {}).get("parked")

    def tick(self):
        """One tick: what it printed and the tmux options it wrote, one tuple each."""
        self.writes.clear()
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.ft.waiting()
        sets, cmd = [], []
        for arg in [a for args in self.writes for a in (*args, ";")]:
            if arg == ";":
                sets.append(tuple(cmd))
                cmd = []
            else:
                cmd.append(arg)
        return out.getvalue(), sets

    def test_a_dialog_is_a_screen_that_ends_on_key_hints(self):
        first = self.ft.dialog
        self.assertEqual(first(TRUST.splitlines()), "Accessing workspace:")
        self.assertEqual(first(CHROME.splitlines()), CHROME.splitlines()[0].strip()[:80])
        self.assertEqual(first(SESSIONS.splitlines()), "", "a list has no key hints")
        self.assertEqual(first(WORKING.splitlines()), "", "a session at work")
        self.assertEqual(first((TRUST.rstrip() + "\n" + "a reply\n" * 8).splitlines()), "",
                         "hints above the bottom 8 rows are quoted text")
        self.assertEqual(first([]), "")

    def test_a_start_up_prompt_needs_you_once_it_has_stood_a_minute(self):
        self.pane("900", 1, "2.1.328", born=str(self.now - 20))
        self.screens["%1"] = TRUST
        out, sets = self.tick()
        self.assertEqual(out, "", "a launch 20 s old may still be answered")
        self.assertEqual(sets, [])
        self.assertAlmostEqual(self.seen(), self.now, delta=5, msg="first seen")
        self.assertFalse(self.file.exists())

        self.seen(self.now - 61)
        self.pane_list[0][13] = str(self.now - 81)
        out, sets = self.tick()
        self.assertIn("⚑ 1 needs you", out)
        self.assertEqual(sets, [("set-option", "-p", "-t", "%1", "@flowkit_state", "waiting"),
                                ("set-option", "-w", "-t", "@1", "@flowkit_wait", "1"),
                                ("set-option", "-w", "-t", "@1", "@flowkit_tab", "needs you")])
        listed = json.loads(self.file.read_text())
        self.assertAlmostEqual(listed["at"], self.now, delta=5)
        self.assertEqual(listed["panes"], [{"pane": "%1", "tab": "w", "index": "1", "story": "st",
                                            "line": "Accessing workspace:", "since": self.now - 61}])

        self.pane_list[0][8], self.windows["@1"] = "waiting", "needs you"
        out, sets = self.tick()
        self.assertIn("⚑ 1 needs you", out)
        self.assertEqual([s for s in sets if "@flowkit_wait" not in s], [], "marked once, not again at every tick")
        self.assertTrue(self.file.exists())

        # answered: the session comes up, claude agents lists it
        self.screens["%1"] = WORKING
        self.rows.append({"kind": "interactive", "pid": 900, "status": "busy", "name": "st-s2"})
        out, sets = self.tick()
        self.assertEqual(out, "")
        self.assertIsNone(self.seen())
        self.assertIn(("set-option", "-p", "-t", "%1", "@flowkit_state", "working"), sets)
        self.assertIn(("set-option", "-w", "-t", "@1", "@flowkit_tab", "running"), sets)
        self.assertFalse(self.file.exists(), "the file drops a pane that no longer waits")

    def test_a_launch_stamped_a_minute_ago_needs_you_at_first_sight(self):
        self.window("@2")  # no story: the pane and the count, no tab word
        self.pane("900", 1, "2.1.328", born=str(self.now - 61))
        self.screens["%1"] = CHROME
        out, sets = self.tick()
        self.assertIn("⚑ 1 needs you", out)
        self.assertIn(("set-option", "-p", "-t", "%1", "@flowkit_state", "waiting"), sets)
        self.assertNotIn("@flowkit_tab", [s[4] for s in sets])
        self.assertEqual(json.loads(self.file.read_text())["panes"][0]["story"], "")

    def test_a_claude_pane_with_no_dialog_on_it_is_left_alone(self):
        self.pane("900", 1, "2.1.328", born=str(self.now - 3600))  # `claude agents`, a picker, a `-p` job
        self.screens["%1"] = SESSIONS
        self.pane("901", 2, "2.1.328", born=str(self.now - 3600))  # a session claude agents has not listed yet
        self.screens["%2"] = WORKING
        self.assertEqual(self.tick(), ("", []))
        self.assertFalse(self.file.exists())

    def test_a_pane_used_again_starts_from_nothing(self):
        self.pane("900", 1, "zsh")  # its claude exited at the prompt
        self.seen(self.now - 3600)
        self.screens["%1"] = TRUST  # still on the screen, above the shell's prompt
        self.file.write_text(json.dumps({"at": self.now - 10, "panes": [{"pane": "%1"}]}))
        self.assertEqual(self.tick(), ("", []))
        self.assertFalse(self.file.exists())
        self.assertIsNone(self.seen())
        self.pane_list[0][7] = "2.1.328"  # a claude started there by hand
        self.assertEqual(self.tick(), ("", []), "its prompt is new: a minute from now")
        self.assertAlmostEqual(self.seen(), self.now, delta=5)
        self.seen(self.now - 3600)
        self.pane_list[0][13] = str(self.now - 20)  # a launch typed there since: the dialog is that session's
        self.assertEqual(self.tick(), ("", []))
        self.assertAlmostEqual(self.seen(), self.now, delta=5)
        self.pane_list.pop()
        self.tick()
        self.assertAlmostEqual(self.seen(), self.now, delta=5, msg="no pane listed at all: tmux did not answer, nothing has gone")
        self.pane("901", 2, "zsh")
        self.tick()
        self.assertIsNone(self.seen(), "a pane that is gone keeps no stamp")

    def test_a_dialog_s_stamp_is_kept_beside_what_the_launch_wrote(self):
        self.window("@1", "")  # the tab has no mark: the launch's record says whose pane it is
        self.pane("900", 1, "2.1.328")
        self.b.launched("tmux", "%1", "st")
        self.b.keep(lambda doc: doc["places"]["tmux %1"].update(born=self.now - 61))
        self.screens["%1"] = TRUST
        self.assertIn("⚑ 1 needs you", self.tick()[0], "launched a minute ago, by the file's stamp")
        self.assertEqual(json.loads(self.file.read_text())["panes"][0]["story"], "st")
        self.assertEqual(self.b.kept()["places"]["tmux %1"], {"story": "st", "adhoc": False, "born": self.now - 61, "parked": self.seen()})
        self.screens["%1"] = ""
        self.tick()
        self.assertEqual(self.b.kept()["places"]["tmux %1"], {"story": "st", "adhoc": False, "born": self.now - 61},
                         "the dialog went, the launch's record stays until a session takes it")
        self.pane_list.pop()
        self.pane("901", 2, "zsh")
        self.tick()
        self.assertEqual(self.b.kept()["places"], {}, "nothing is kept of a pane that is gone")

    def test_what_a_tick_before_0_15_0_kept_in_the_pane_still_counts(self):
        self.pane("900", 1, "2.1.328", parked=str(self.now - 61))
        self.screens["%1"] = TRUST
        self.assertIn("⚑ 1 needs you", self.tick()[0])
        self.assertEqual(json.loads(self.file.read_text())["panes"][0]["since"], self.now - 61)
        self.assertIsNone(self.seen(), "the file gets no stamp of its own while the pane's is read")

    def test_the_list_shows_a_parked_pane_among_those_that_wait(self):
        self.pane("900", 1, "2.1.328", born=str(self.now - 61))
        self.screens["%1"] = TRUST
        self.ft.input = lambda prompt="": "1"
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.ft.list_sessions()
        self.assertRegex(out.getvalue(), r"\n  1   work:w\.1 +Accessing workspace: +NEEDS YOU \(start-up\) +0m\n")
        self.assertEqual(self.writes[-1], ("switch-client", "-t", "%1"), "its number jumps there")


class Asks(Labels):
    """The marks ask `claude agents` once a call (the tick decides how often: tests/test_tick.py),
    `list` asks every time, and a program that is not installed is one that failed."""

    def setUp(self):
        super().setUp()
        self.window("@1", "st")

    def test_marks_print_the_line_and_ask_once(self):
        self.session(1, status="waiting")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.ft.main(["marks"])
        self.assertEqual((out.getvalue(), self.asked), ("#[reverse] ⚑ 1 needs you #[noreverse] ", 1))

    def test_list_asks_every_time(self):
        self.session(1, status="waiting")
        self.ft.input = lambda prompt="": ""
        with contextlib.redirect_stdout(io.StringIO()):
            self.ft.list_sessions()
            self.ft.list_sessions()
        self.assertEqual(self.asked, 2)

    def test_a_program_that_is_not_installed_ends_no_pass(self):
        """No tmux, or no claude, on the PATH raised out of the marks and took the whole tick along."""
        ft = load()
        b = backend(ft, self.tmp)

        def missing(*cmd):
            raise FileNotFoundError("flowkit-no-such-program")
        b.tmux = b.agents = b.parents = missing  # what every call does on a computer with neither
        self.assertIsNone(ft.ask(b.TMUX.places))
        ft.CLAUDE = ft.CONFIG = self.tmp
        with contextlib.redirect_stdout(io.StringIO()) as out:
            ft.main(["marks"])
        self.assertEqual(out.getvalue(), "")


class Closer(Labels):
    """autoclose=on: a finished story's tab goes as a whole, reporter last; anything else stays."""

    def setUp(self):
        super().setUp()
        (self.tmp / "handoff.conf").write_text("driver=tmux\nautoclose=on\n")
        self.killed = []
        self.ft.terminate = lambda pid: self.killed.append(pid) or True
        self.window("@1", "st")

    def note(self, *lines, age=10 * 60):
        path = self.tmp / "stories" / "st.md"
        with path.open("a") as f:
            f.write("".join(f"{line}\n" for line in lines))
        os.utime(path, (time.time() - age, time.time() - age))

    def shell(self, n, born=""):
        self.pane(str(5000 + n), n, "zsh", born)

    def closes(self):
        self.writes.clear()
        self.listed = 0
        self.ft.closed.cache_clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.ft.waiting()
        return [a[2] for a in self.writes if a[0] == "kill-pane"]

    def rang(self):
        return any("report now" in a for a in self.writes)

    def finished(self):
        """A story of two sessions, both closed, with its reporter's final report in."""
        self.note("# st", "Next: spawned s2 (opus/high)")
        self.session(2, status="idle", idle_for=10 * 60, closed=True)
        self.session(1, status="idle", idle_for=10 * 60, name="st-report")
        self.session(3, status="idle", idle_for=10 * 60, closed=True)
        self.note(self.final())

    def final(self):
        return f"## Final report · {time.strftime('%Y-%m-%d %H:%M', time.localtime(time.time() - 3600))}"

    def test_a_finished_story_goes_reporter_last(self):
        self.finished()
        self.shell(4)
        self.assertEqual(self.closes(), ["%4", "%2", "%3", "%1"])
        self.assertEqual(self.killed, [1002, 1003, 1001])

    def test_a_final_report_naming_missing_sessions_ends_the_wait(self):
        self.note("# st", "Next: spawned s2, s9")
        self.session(2, status="idle", idle_for=20 * 60, closed=True)
        self.session(1, status="idle", idle_for=20 * 60, name="st-report")
        self.session(3, status="idle", idle_for=20 * 60)  # never closed
        self.assertEqual(self.closes(), [])
        self.assertTrue(self.rang())
        self.note(self.final(), "s9 never closed; s3 recorded from its transcript")
        self.assertEqual(self.closes(), ["%2", "%3", "%1"])

    def test_a_session_started_after_the_final_report_needs_its_own_close(self):
        self.finished()
        self.session(5, status="idle", idle_for=10 * 60)
        self.rows[-1]["startedAt"] = (time.time() - 30 * 60) * 1000
        self.assertEqual(self.closes(), [])

    def test_a_complete_story_rings_its_reporter_until_the_report_is_in(self):
        self.note("# st", "Next: spawned s2")
        self.session(2, status="idle", idle_for=10 * 60, closed=True)
        reporter = self.session(1, status="idle", idle_for=10 * 60, name="st-report")
        self.pane_list[-1][12] = str(int(time.time()))
        self.assertEqual(self.closes(), [])
        self.assertFalse(self.rang(), "a tick before 0.15.0 rang it and kept that in the pane")
        self.pane_list[-1][12] = ""
        self.closes()
        self.assertEqual([a for a in self.writes if a[0] == "send-keys"], [("send-keys", "-t", "%1", "C-u", ";", "send-keys", "-t", "%1", "-l", "report now",
                                        ";", "run-shell", "sleep 0.5", ";", "send-keys", "-t", "%1", "Enter")])
        self.assertAlmostEqual(self.b.kept()["sessions"][reporter]["rung"], time.time(), delta=5)
        self.closes()
        self.assertFalse(self.rang())  # rung a moment ago: not again yet

    def test_an_open_watch_keeps_the_story_and_its_reporter_unrung(self):
        self.note("# st")
        sid = self.session(2, status="idle", idle_for=20 * 60)
        self.note("## s2 · 2026-11-17 15:20", f"Session: {sid} · Repo: /repo",
                  "Next: watching app!7 (https://gitlab.com/g/p/-/merge_requests/7)")
        reporter = self.session(1, status="idle", idle_for=20 * 60, name="st-report")
        arm = self.arm("app!7")
        self.assertEqual(self.closes(), ["%2"], "the slice's pane goes; the story and its reporter stay")
        self.assertFalse(self.rang(), "a watch longer than the backfill's 15 minutes")
        self.rows.pop(0), self.pane_list.pop(0)
        self.assertEqual(self.closes(), [])
        self.assertFalse(self.rang())
        arm.unlink()  # gone with no section: the promise is still open, and the backfill is back
        self.assertEqual(self.closes(), [])
        self.assertTrue(self.rang())
        self.note("## app!7 · 2026-11-17 16:05", "Watch: pipeline green 16:04", "Next: none")
        self.b.note(reporter, rung=0)
        self.assertEqual(self.closes(), [])
        self.assertTrue(self.rang(), "complete: the reporter writes its final report")
        self.note(self.final())
        self.assertEqual(self.closes(), ["%1"])

    def test_a_tab_whose_claudes_exited_still_closes(self):
        self.note("# st", "Next: spawned s2")
        self.shell(1)
        self.shell(2)
        self.assertEqual(self.closes(), ["%1", "%2"])
        self.assertEqual(self.killed, [])

    def test_a_tab_flowkit_spawn_just_opened_stays_until_its_sessions_come_up(self):
        self.note("# st", "Next: spawned s2")
        self.shell(1, born=str(int(time.time())))
        self.shell(2, born=str(int(time.time()) - 5))
        self.assertEqual(self.closes(), [])
        for p in self.pane_list:
            p[13] = str(int(time.time() - self.ft.SPAWN_GRACE - 1))
        self.assertEqual(self.closes(), ["%1", "%2"])

    def test_a_launch_the_file_alone_knows_of_keeps_its_tab_too(self):
        self.note("# st", "Next: spawned s2")
        self.shell(1)
        self.shell(2)
        self.b.launched("tmux", "%2", "st")
        self.assertEqual(self.closes(), [], "no stamp in tmux: the launch's record in the file is read")
        self.b.keep(lambda doc: doc["places"]["tmux %2"].update(born=int(time.time() - self.ft.SPAWN_GRACE - 1)))
        self.assertEqual(self.closes(), ["%1", "%2"])

    def test_a_shell_just_reused_in_a_finished_tab_keeps_the_story(self):
        self.finished()
        self.shell(4, born=str(int(time.time())))
        self.assertEqual(self.closes(), [])
        self.assertEqual(self.killed, [])

    def test_a_launch_that_landed_since_the_pane_list_keeps_the_story(self):
        self.finished()
        self.shell(4)
        self.later = [list(p) for p in self.pane_list]
        self.later[-1][13] = str(int(time.time()))  # stamped between the list and the kill
        self.assertEqual(self.closes(), [])
        self.later[-1][13] = ""
        self.later[-1][7] = "2.1.304"  # its claude is up, not yet in claude agents
        self.assertEqual(self.closes(), [])
        self.assertEqual(self.killed, [])
        self.later = None
        self.assertEqual(self.closes(), ["%4", "%2", "%3", "%1"])

    def test_a_session_whose_transcript_or_fork_moved_lately_is_not_killed(self):
        self.finished()
        transcript = next((self.tmp / "projects" / "-repo").glob("00000003-*.jsonl"))
        os.utime(transcript, None)
        self.assertEqual(self.closes(), [])
        os.utime(transcript, (time.time() - 600, time.time() - 600))
        forks = transcript.with_suffix("") / "subagents"
        forks.mkdir(parents=True)
        (forks / "agent-a.jsonl").write_text("{}\n")
        self.assertEqual(self.closes(), [])

    def test_a_fresh_story_file_waits_out_the_grace(self):
        self.finished()
        self.note(age=60)
        self.writes.clear()
        with contextlib.redirect_stdout(io.StringIO()):
            self.ft.waiting()
        self.assertEqual([a for a in self.writes if a[0] == "kill-pane"], [])

    def test_watching_working_and_held_tabs_stay(self):
        self.finished()
        self.session(5, status="busy", live="shell", closed=True)
        self.assertEqual(self.closes(), [])
        for hold in ("@flowkit_hold", "@cc_hold"):
            self.setUp()
            self.window("@1", "st", hold="1")
            self.finished()
            self.assertEqual(self.closes(), [], hold)

    def test_a_pane_running_something_else_keeps_the_story(self):
        self.finished()
        self.pane("6000", 6, "vim")
        self.assertEqual(self.closes(), [])

    def test_workspace_and_adhoc_windows_are_never_touched(self):
        self.window("@2")
        self.shell(1)
        self.session(2, status="idle", idle_for=60 * 60)
        self.window("@3", "adhoc")
        self.session(3, status="idle", idle_for=60 * 60, closed=True)
        self.assertEqual(self.closes(), [])
        self.assertEqual(self.killed, [])

    def test_autoclose_off_kills_nothing(self):
        for conf in ("driver=tmux\n", "autoclose=on\nautoclose=off\n"):
            (self.tmp / "handoff.conf").write_text(conf)
            self.finished()
            self.shell(4)
            self.assertEqual(self.closes(), [], conf)
            self.assertFalse(self.rang())
            self.setUp()
            (self.tmp / "handoff.conf").write_text(conf)


class PaneCloser(Closer):
    """autoclose=on: a finished slice's pane goes once its close is recorded and its successor runs."""

    def slice_done(self, n=1, nxt="s2", name="", **kw):
        """Session n idle 10 minutes with its close recorded, `Next: started <nxt>`."""
        sid = self.session(n, status=kw.pop("status", "idle"), idle_for=10 * 60, name=name or f"s{n}", **kw)
        self.note(f"## s{n} · 2026-11-13 09:00", f"Session: {sid} · Repo: /repo", f"Next: started {nxt}")
        return sid

    def successor(self, n=2, status="busy"):
        self.session(n, status=status, name=f"s{n}")

    def test_a_finished_slice_goes_once_its_successor_runs(self):
        self.slice_done()
        self.successor()
        self.assertEqual(self.closes(), ["%1"])
        self.assertEqual(self.killed, [1001])

    def test_a_successor_in_the_background_counts(self):
        self.slice_done()
        self.rows.append({"kind": "background", "pid": 4242, "name": "s2", "status": "busy"})
        self.assertEqual(self.closes(), ["%1"])

    def test_a_slice_that_registered_a_watch_goes_while_the_watch_is_open(self):
        sid = self.session(1, status="idle", idle_for=10 * 60, name="s1")
        self.note("## s1 · 2026-11-17 15:20", f"Session: {sid} · Repo: /repo",
                  "Next: watching app!7 (https://gitlab.com/g/p/-/merge_requests/7)")
        self.session(9, status="idle", idle_for=60, name="st-report")
        self.assertEqual(self.closes(), [], "no arm is open")
        self.arm("app!7", story="other")
        self.assertEqual(self.closes(), [], "another story's arm")
        self.arm("app!7")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.ft.closable()
        self.assertEqual(out.getvalue(), "would close work:w.1 %1 s1 (watch app!7 is open)\n")
        self.assertEqual(self.closes(), ["%1"])
        self.assertEqual(self.killed, [1001])

    def test_no_successor_running_or_no_close_recorded_keeps_the_pane(self):
        self.slice_done()
        self.assertEqual(self.closes(), [], "the successor is not running")
        self.setUp()
        self.session(1, status="idle", idle_for=10 * 60, name="s1")
        self.note("# st", "Next: started s2")  # a line outside its close section
        self.successor()
        self.assertEqual(self.closes(), [], "no close recorded")

    def test_the_reporter_and_a_coordinator_stay(self):
        self.slice_done(name="st-report")
        self.successor()
        self.assertEqual(self.closes(), [], "reporter")
        for name in ("st-coord", "st-coordinator", "coord-7"):
            self.setUp()
            self.slice_done(name=name)
            self.successor()
            self.assertEqual(self.closes(), [], name)
        self.setUp()
        (self.tmp / "coordination").mkdir()
        (self.tmp / "coordination" / "st.md").write_text("# st - coordination board\nCoordinator: `lead` · Mission: x\n")
        self.slice_done(name="lead")
        self.successor()
        self.assertEqual(self.closes(), [], "named on a coordination board")

    def test_a_busy_waiting_or_lately_touched_session_stays(self):
        for kw in ({"status": "busy"}, {"status": "waiting"}, {"touched": 60}, {"retry": True},
                   {"retry": {"kind": "rate_limit"}}):
            self.setUp()
            self.slice_done(**kw)
            self.successor()
            self.assertEqual(self.closes(), [], kw)
            self.assertEqual(self.killed, [], kw)

    def test_adhoc_unmarked_and_held_tabs_stay(self):
        for story, hold in (("adhoc", ""), ("", ""), ("st", "1")):
            self.setUp()
            self.window("@2", story, hold=hold)
            self.slice_done()
            self.successor()
            self.assertEqual(self.closes(), [], (story, hold))

    def test_autoclose_off_closes_no_pane(self):
        (self.tmp / "handoff.conf").write_text("driver=tmux\n")
        self.slice_done()
        self.successor()
        self.assertEqual(self.closes(), [])

    def test_closable_prints_and_closes_nothing(self):
        self.slice_done()
        self.successor()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.ft.closable()
        self.assertEqual(out.getvalue(), "would close work:w.1 %1 s1 (successor s2 runs)\n")
        self.assertEqual((self.killed, [a for a in self.writes if a[0] == "kill-pane"]), ([], []))


if __name__ == "__main__":
    unittest.main()
