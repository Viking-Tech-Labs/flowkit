"""Run with `python3 -m unittest discover plugins/handoff/tests`. backend.py loaded by its path, as
a command copied out of the plugin loads it, with a `tmux`, a `ps` and a `claude` of the test's own
that answer from files and record what they were called with: every request, asked of the tmux
backend and of the background one. No pause is waited for: the module's `time.sleep` only records."""
import importlib.util
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parent.parent
BACKEND = PLUGIN / "scripts" / "backend.py"
PANES = "tmux list-panes -a -F #{pane_pid} #{pane_id} #{pane_in_mode}"
RULE = "─" * 20
SEP = "\x1f"
SID = "0a1b2c3d-0000-4000-8000-000000000001"


def load():
    spec = importlib.util.spec_from_file_location("flowkit_backend", BACKEND)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, d, True)
        (d / "bin").mkdir()
        self.stub("tmux", f'echo "tmux $*" >> {d}/calls\n[ "$1" = -L ] && shift 2\ncase "$1" in\n'
                          f"list-panes) cat {d}/panes;;\ncapture-pane) cat {d}/screen;;\ndisplay-message) cat {d}/story;;\n"
                          f"list-windows) cat {d}/windows;;\n"
                          f"list-clients) cat {d}/clients;;\nsend-keys) [ -f {d}/refuse ] && exit 1;;\nesac\nexit 0\n")
        self.stub("ps", f"cat {d}/ps\n")
        self.stub("claude", f'case "$1" in\nagents) echo "claude $*" >> {d}/calls; cat {d}/agents.json;;\n'
                            f'stop) echo "claude $*" >> {d}/calls; [ -f {d}/stopped.json ] && mv {d}/stopped.json {d}/agents.json;;\n'
                            f'*) echo "claude $* (in $(pwd -P))" >> {d}/calls; [ -f {d}/refuse ] && exit 1;;\nesac\nexit 0\n')
        for name in ("panes", "screen", "story", "clients", "ps", "windows"):
            (d / name).write_text("")
        self.agents([])
        self.env = {"PATH": f"{d / 'bin'}:/usr/bin:/bin"}
        patch = mock.patch.dict(os.environ, self.env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.slept = []
        self.b = load()
        self.b.time = mock.Mock(sleep=self.slept.append, time=time.time)  # its own pauses alone: subprocess sleeps while it waits, too
        self.b.STATE = d / "claude" / "flowkit" / "sessions.json"

    def stub(self, name, body):
        path = self.tmp / "bin" / name
        path.write_text(f"#!/bin/sh\n{body}")
        path.chmod(0o755)

    def agents(self, rows):
        (self.tmp / "agents.json").write_text(json.dumps(rows))

    def calls(self):
        path = self.tmp / "calls"
        lines = path.read_text().splitlines() if path.exists() else []
        path.unlink(missing_ok=True)
        return lines

    def without(self, program):
        """The same world with that program not installed."""
        (self.tmp / "bin" / program).unlink()
        return mock.patch.dict(os.environ, {"PATH": str(self.tmp / "bin")})


class TmuxBackend(Case):
    def setUp(self):
        super().setUp()
        self.t = self.b.TMUX
        self.pane = self.b.Place("%7", False, None)

    def world(self, panes, processes):
        (self.tmp / "panes").write_text("".join(f"{row}\n" for row in panes))
        (self.tmp / "ps").write_text("".join(f"{pid} {parent}\n" for pid, parent in processes))

    def shows(self, *rows):
        (self.tmp / "screen").write_text("".join(f"{row}\n" for row in rows))

    def box(self, line, below=()):
        self.shows("an answer", f"\x1b[2m{RULE}\x1b[0m", line, f"\x1b[2m{RULE}\x1b[0m", *below)

    def test_available_follows_the_program(self):
        self.assertTrue(self.t.available())
        with self.without("tmux"):
            self.assertFalse(self.t.available())
            with self.assertRaises(OSError):
                self.t.locate(pid=100)

    def test_locate_finds_the_pane_through_the_parent_processes_and_says_copy_mode(self):
        self.world(["100 %1 0", "200 %2 1", "junk"], [(101, 100), (102, 101), (103, 102), (104, 103), (201, 200), (300, 1)])
        P = self.b.Place
        self.assertEqual(self.t.locate(pid=100), P("%1", False, None), "the pane's own process")
        self.assertEqual(self.calls(), [PANES])
        self.assertEqual(self.t.locate(pid=103), P("%1", False, None), "three processes below the pane's shell")
        self.assertIsNone(self.t.locate(pid=104), "four below is another program's child")
        self.assertEqual(self.t.locate(pid=201), P("%2", True, None), "in copy mode")
        self.assertTrue(self.t.locate(pid=201).copy)
        for pid in (300, 999, None):
            self.assertIsNone(self.t.locate(pid=pid))
        self.assertIsNone(self.t.locate(sid=SID, name="st-report"), "a pane is found by the process alone")

    def test_one_finder_reads_the_panes_once_for_any_number_of_sessions(self):
        self.world(["100 %1 0", "200 %2 0"], [(101, 100), (201, 200)])
        find = self.t.finder()
        self.assertEqual([find(pid) and find(pid).id for pid in (101, 201, 5, 101)], ["%1", "%2", None, "%1"])
        self.assertEqual(self.calls(), [PANES])

    def test_here_is_the_pane_of_this_process(self):
        self.assertIsNone(self.t.here())
        with mock.patch.dict(os.environ, {"TMUX_PANE": "%3"}):
            self.assertEqual(self.t.here(), self.b.Place("%3", False, None))
        self.assertEqual(self.calls(), [])

    def test_another_server_is_asked_through_its_socket(self):
        with mock.patch.dict(os.environ, {"FLOWKIT_TMUX_SOCKET": "flowkit-x"}):
            self.t.last_input()
        self.assertEqual(self.calls(), ["tmux -L flowkit-x list-clients -F #{client_activity}"])

    def test_screen_tells_what_is_typed_from_what_is_suggested(self):
        self.box("\x1b[39m❯\xa0\x1b[2mship it\x1b[0m")
        rows = self.t.screen(self.pane)
        self.assertEqual(self.calls(), ["tmux capture-pane -p -e -t %7"])
        self.assertEqual(rows[0], ("an answer", "an answer"))
        self.assertEqual(rows[1], (RULE, " " * 20), "a dim rule is no text")
        self.assertEqual(rows[2], ("❯\xa0ship it", "❯\xa0       "))
        self.assertEqual(self.b.drafted(rows[2]), "")
        self.assertEqual(self.b.drafted(self.b.rows("❯ typed \x1b[2mand dim\x1b[0m")[0]), "typed")
        # an attribute holds over the end of a row until it is changed
        self.assertEqual(self.b.rows("\x1b[2mdim\nstill dim\x1b[22m plain")[1], ("still dim plain", "          plain"))

    def test_bottom_is_the_last_rows_without_the_blank_ones(self):
        self.shows(*(f"row {n}" for n in range(1, 13)), "", "  ")
        self.assertEqual(self.t.bottom(self.pane), [f"row {n}" for n in range(5, 13)])
        self.assertEqual(self.calls(), ["tmux capture-pane -p -t %7"])

    def test_parked_and_draft_read_the_prompt_box(self):
        for line, below, why, what in (
                ("\x1b[39m❯\xa0", (), "", "an empty box"),
                ("\x1b[39m❯\xa0\x1b[2mship it\x1b[0m", (), "", "a suggestion"),
                ("\x1b[39m❯\xa0half typed", (), "draft", "a draft"),
                ("\x1b[39m❯\xa0", ("  \x1b[2mEnter to confirm · Esc to cancel\x1b[0m",), "dialog", "a dialog below the box"),
                ("\x1b[39m❯\xa0half typed", ("❯ 1. Yes", "  2. No"), "dialog", "a menu wins over a draft")):
            self.box(line, below)
            self.assertEqual((self.t.parked(self.pane), self.t.parked("%7")), (why, why), what)
            self.assertEqual(self.t.draft(self.pane), "half typed" in line, what)
        self.shows("$ a bare shell")
        self.assertEqual((self.t.parked(self.pane), self.t.draft(self.pane)), ("no prompt box", False))
        self.assertEqual(set(self.calls()), {"tmux capture-pane -p -e -t %7"})

    def test_a_line_is_one_tmux_call(self):
        self.assertEqual(self.t.type(self.pane, "carry on"), "")
        self.assertEqual(self.t.type("%7", "carry on", self.b.LINE), "", "a bare pane id is a place")
        line = "tmux send-keys -t %7 -l -- carry on ; run-shell sleep 0.5 ; send-keys -t %7 Enter"
        self.assertEqual(self.calls(), [line, line])
        self.assertEqual(self.t.type(self.pane, "closed s1", self.b.OVER), "")
        self.assertEqual(self.calls(), ["tmux send-keys -t %7 C-u ; send-keys -t %7 -l -- closed s1 ; run-shell sleep 0.5 ; "
                                        "send-keys -t %7 Enter"])
        (self.tmp / "refuse").write_text("")
        self.assertEqual([self.t.type(self.pane, "x", how) for how in (self.b.LINE, self.b.OVER)], ["not typed"] * 2)
        self.assertEqual(self.slept, [], "the pause before Enter is tmux's")

    def test_a_line_for_a_free_box_is_refused_where_something_is_in_its_way(self):
        for how in (self.b.FREE, self.b.COMMAND):
            self.box("\x1b[39m❯\xa0")
            self.assertEqual(self.t.type(self.b.Place("%7", True, None), "/remote-control", how), "copy mode")
            self.assertEqual(self.calls(), [], "copy mode is known from locate: nothing is asked")
            for line, below, why in (("\x1b[39m❯\xa0mine", (), "draft"), ("\x1b[39m❯\xa0", ("Esc to cancel",), "dialog")):
                self.box(line, below)
                self.assertEqual(self.t.type(self.pane, "/remote-control", how), why)
            self.shows("$")
            self.assertEqual(self.t.type(self.pane, "/remote-control", how), "no prompt box")
            self.assertEqual(self.calls(), ["tmux capture-pane -p -e -t %7"] * 3, "and nothing is typed")
            self.box("\x1b[39m❯\xa0\x1b[2ma suggestion\x1b[0m")
            self.assertEqual(self.t.type(self.pane, "/remote-control", how), "")
            self.assertEqual(self.calls(), ["tmux capture-pane -p -e -t %7", "tmux send-keys -t %7 -l -- /remote-control ; "
                                            "run-shell sleep 0.5 ; send-keys -t %7 Enter"])
            (self.tmp / "refuse").write_text("")
            self.assertEqual(self.t.type(self.pane, "/remote-control", how), "not typed")
            (self.tmp / "refuse").unlink()
            self.calls()

    def test_escape_first_ends_the_question_then_types_over_what_is_there(self):
        asked = []
        self.assertEqual(self.t.type(self.pane, "away: carry on", self.b.ESCAPE, settled=lambda: asked.append(1) or True), "")
        self.assertEqual(self.calls(), ["tmux send-keys -t %7 Escape", "tmux send-keys -t %7 C-u ; send-keys -t %7 -l -- "
                                        "away: carry on ; run-shell sleep 0.5 ; send-keys -t %7 Enter"])
        self.assertEqual((asked, self.slept), ([1], [self.b.PAUSE]), "asked once, after the Escape")
        self.assertEqual(self.t.type(self.pane, "away: carry on", self.b.ESCAPE, settled=lambda: False), "not settled")
        self.assertEqual(self.calls(), ["tmux send-keys -t %7 Escape"], "a session still on its question gets no line")
        self.assertEqual(self.t.type(self.pane, "away: carry on", self.b.ESCAPE), "", "nothing to wait for")
        self.assertEqual(len(self.calls()), 2)

    def test_escape_alone_story_and_the_last_input(self):
        self.assertTrue(self.t.escape(self.pane))
        self.assertEqual(self.calls(), ["tmux send-keys -t %7 Escape"])
        (self.tmp / "refuse").write_text("")
        self.assertFalse(self.t.escape(self.pane))
        (self.tmp / "story").write_text(f"my-story{SEP}\n")
        self.assertEqual(self.t.story(self.pane), "my-story", "a session that registered nothing: what its tab is marked with")
        self.assertEqual(self.calls()[-1], f"tmux display-message -p -t %7 #{{@flowkit_story}}{SEP}#{{@flowkit_adhoc}}")
        self.assertEqual(self.t.marked("%7"), ("my-story", False))
        (self.tmp / "story").write_text(f"{SEP}adhoc\n")
        self.assertEqual((self.t.story("%7"), self.t.marked("%7")), ("", ("", True)))
        self.calls()
        self.b.register(SID, "tmux", "%7", "its-story")
        self.assertEqual((self.t.story(self.pane), self.t.story("%7"), self.calls()), ("its-story", "its-story", []),
                         "registered: the file answers and tmux is not asked")
        (self.tmp / "story").write_text("work\n")
        self.assertEqual(self.t.session(), "work")
        self.calls()
        self.assertIsNone(self.t.last_input(), "no client attached")
        (self.tmp / "clients").write_text("1700000000\n1700000300\n\n")
        self.assertEqual(self.t.last_input(), 1700000300)


    def test_places_lists_every_pane_with_what_tmux_keeps_for_it(self):
        row = ["900", "%1", "work", "@1", "1", "my-story", "2", "2.1.328", "my-story", "my-story", "1", "1700000000", "", ""]
        (self.tmp / "panes").write_text(SEP.join(row) + "\n\n")
        keys = ("pid", "id", "sess", "wid", "widx", "wname", "pidx", "cmd", "story", "slug", "hold", "born", "rung", "parked")
        self.assertEqual(self.t.places(), [dict(zip(keys, row))])
        self.assertEqual(len(self.calls()), 1)

    def test_label_writes_what_differs_from_what_is_shown_in_one_call(self):
        shown = [("p", "%1", "idle"), ("p", "%2", "waiting"), ("p", "%3", ""), ("p", "%4", "idle"),
                 ("w", "@1", "1", "running"), ("w", "@2", "", "done"), ("w", "@3", "", ""), ("w", "@4", "2", "error")]
        (self.tmp / "panes").write_text("".join(SEP.join(row) + "\n" for row in shown))
        self.t.label({"%1": "working", "%3": "idle", "%4": "idle"}, {"@1": (1, "needs you"), "@3": (2, ""), "@4": (2, "error")})
        self.assertEqual(self.calls()[1:], [
            "tmux set-option -p -t %1 @flowkit_state working ; set-option -p -u -t %2 @flowkit_state ; "
            "set-option -p -t %3 @flowkit_state idle ; set-option -w -t @1 @flowkit_tab needs you ; "
            "set-option -w -u -t @2 @flowkit_tab ; set-option -w -t @3 @flowkit_wait 2"])
        self.t.label({"%1": "idle", "%2": "waiting", "%4": "idle"}, {"@1": (1, "running"), "@2": (0, "done"), "@4": (2, "error")})
        self.assertEqual(len(self.calls()), 1, "nothing differs: the reading alone")

    def test_close_and_pick(self):
        self.assertTrue(self.t.close(self.pane))
        self.assertTrue(self.t.pick("%7", "/dev/ttys001"))
        self.assertTrue(self.t.pick(self.pane))
        self.assertEqual(self.calls(), ["tmux kill-pane -t %7", "tmux switch-client -c /dev/ttys001 -t %7",
                                        "tmux switch-client -t %7"])


class BackgroundBackend(Case):
    def setUp(self):
        super().setUp()
        self.g = self.b.BACKGROUND
        self.repo = self.tmp / "repo"
        self.repo.mkdir()

    def row(self, **more):
        return {"kind": "background", "name": "st-report", "id": "b1", "sessionId": SID, "cwd": str(self.repo),
                "startedAt": 5, **more}

    def place(self, **more):
        self.agents([self.row(**more)])
        found = self.g.locate(sid=SID)
        self.calls()
        return found

    def resume(self, text):
        return f"claude --bg --resume {SID} {text} (in {self.repo.resolve()})"

    def test_available_follows_the_program(self):
        self.assertTrue(self.g.available())
        with self.without("claude"):
            self.assertFalse(self.g.available())
            with self.assertRaises(OSError):
                self.g.locate(sid=SID)

    def test_locate_is_the_newest_entry_by_session_id_or_name_stopped_ones_too(self):
        old, new = self.row(startedAt=1, id="b0"), self.row(startedAt=9, id="b2", pid=4242, status="idle")
        other = self.row(sessionId="other", name="st-s2", id="b3")
        self.agents([old, {**new, "kind": "interactive", "id": "i1", "startedAt": 99}, new, other, "junk"])
        found = self.g.locate(sid=SID)
        self.assertEqual(found, self.b.Place(SID, False, new))
        self.assertFalse(found.copy, "a background session has no copy mode")
        self.assertEqual(self.calls(), ["claude agents --json --all"])
        self.assertEqual(self.g.locate(name="st-report").row["id"], "b2")
        self.assertEqual(self.g.locate(sid="other", name="st-report").row["id"], "b3", "a session id wins over a name")
        self.assertEqual(self.g.locate(pid=4242, name="st-s2").row["id"], "b3", "never by a process")
        self.assertIsNone(self.g.locate(name="gone-report"))
        self.calls()
        self.assertIsNone(self.g.locate(pid=4242))
        self.assertEqual(self.calls(), [], "nothing to look for")
        (self.tmp / "agents.json").write_text("{")
        with self.assertRaises(ValueError):
            self.g.locate(sid=SID)

    def test_it_has_no_screen_no_story_and_no_terminal(self):
        place = self.place(status="idle", pid=4242)
        self.assertEqual((self.g.here(), self.g.screen(place), self.g.bottom(place), self.g.parked(place), self.g.draft(place),
                          self.g.escape(place), self.g.story(place), self.g.last_input(), self.g.places(),
                          self.g.label({}, {}), self.g.close(place), self.g.pick(place)),
                         (None, [], [], "", False, False, "", None, [], None, False, False))
        self.assertEqual(self.calls(), [])
        self.b.register(SID, "background", SID, "night-story")
        self.assertEqual(self.g.story(place), "night-story", "once something registered it: the file is the same for both")

    def test_a_line_resumes_a_stopped_session_under_its_own_id(self):
        place = self.place()
        for how in (self.b.LINE, self.b.OVER, self.b.FREE, self.b.ESCAPE):
            self.assertEqual(self.g.type(place, "closed s1", how), "", how)
            self.assertEqual(self.calls(), [self.resume("closed s1")], "no process to stop")
        self.assertEqual(self.slept, [])
        (self.tmp / "refuse").write_text("")
        self.assertEqual(self.g.type(place, "closed s1"), "not typed")

    def test_what_claude_said_to_a_resume_comes_with_the_answer(self):
        self.stub("claude", "echo 'resumed as a copy under a new id'; echo 'and a warning' >&2\n")
        typed = self.g.type(self.b.Place(SID, False, self.row()), "closed s1")
        self.assertEqual((typed, typed.said.splitlines()), ("", ["resumed as a copy under a new id", "and a warning"]))
        self.assertEqual(getattr(self.b.TMUX.type("%7", "a line"), "said", ""), "", "tmux says nothing")

    def test_a_live_session_is_stopped_first_and_resumed_once_its_process_is_gone(self):
        place = self.place(pid=4242, status="idle")
        (self.tmp / "stopped.json").write_text(json.dumps([self.row()]))
        self.assertEqual(self.g.type(place, "closed s1", self.b.FREE), "")
        self.assertEqual(self.calls(), ["claude stop b1", "claude agents --json --all", self.resume("closed s1")])
        self.assertEqual(self.slept, [self.b.PAUSE])
        place = self.place(pid=4242, status="idle")
        self.slept.clear()
        self.assertEqual(self.g.type(place, "closed s1"), "not stopped", "the process stays: a resume would start a copy")
        calls = self.calls()
        self.assertEqual((calls[0], set(calls[1:])), ("claude stop b1", {"claude agents --json --all"}))
        self.assertEqual(sum(self.slept), self.b.STOPPING)

    def test_a_session_on_a_question_is_left_alone_unless_escape_is_asked_for(self):
        for waiting in ({"status": "waiting"}, {"state": "blocked"}):
            place = self.place(pid=4242, **waiting)
            self.assertEqual(self.g.type(place, "closed s1", self.b.FREE), "waiting")
            self.assertEqual(self.calls(), [], "a stop would throw its question away")
        place = self.place(pid=4242, status="waiting")
        (self.tmp / "stopped.json").write_text(json.dumps([self.row()]))
        self.assertEqual(self.g.type(place, "away: carry on", self.b.ESCAPE), "")
        self.assertEqual(self.calls(), ["claude stop b1", "claude agents --json --all", self.resume("away: carry on")])
        self.assertEqual(self.slept, [self.b.PAUSE, self.b.GRACE], "a resume right after the stop starts a copy")

    def test_a_command_has_no_prompt_box_to_go_into(self):
        place = self.place(status="idle", pid=4242)
        self.assertEqual(self.g.type(place, "/remote-control", self.b.COMMAND), "no prompt box")
        self.assertEqual(self.calls(), [])


class Both(Case):
    def test_mode_is_busy_waiting_or_idle_for_either(self):
        for row, want in (({"kind": "interactive", "status": "busy"}, "busy"),
                          ({"kind": "interactive", "status": "waiting"}, "waiting"),
                          ({"kind": "interactive", "status": "idle"}, "idle"),
                          ({"kind": "background", "status": "idle", "state": "working"}, "idle"),
                          ({"kind": "background", "state": "working"}, "busy"),
                          ({"kind": "background", "state": "blocked"}, "waiting"),
                          ({"kind": "background", "state": "done"}, "idle"), ({"kind": "background"}, "idle")):
            self.assertEqual(self.b.mode(row), want, row)

    def test_agents_is_claudes_own_list(self):
        self.agents([{"name": "s1"}])
        self.assertEqual((self.b.agents(), self.b.agents("--all")), ([{"name": "s1"}],) * 2)
        self.assertEqual(self.calls(), ["claude agents --json", "claude agents --json --all"])
        (self.tmp / "agents.json").write_text("")
        with self.assertRaises(ValueError):
            self.b.agents()

    def test_the_two_backends_answer_the_same_requests(self):
        requests = sorted(name for name, value in vars(self.b.Backend).items() if inspect.isfunction(value))
        self.assertEqual(requests, ["available", "bottom", "close", "draft", "escape", "here", "label", "last_input",
                                    "locate", "open", "parked", "pick", "places", "plan", "screen", "story", "target", "type"])
        for backend in (self.b.TMUX, self.b.BACKGROUND):
            for name in requests:
                self.assertEqual(inspect.signature(getattr(backend, name)), inspect.signature(getattr(self.b.Backend(), name)),
                                 f"{backend.name}.{name}")
        self.assertEqual({self.b.LINE, self.b.OVER, self.b.FREE, self.b.ESCAPE, self.b.COMMAND},
                         {"line", "over", "free", "escape", "command"})

    def test_no_request_names_a_tmux_option(self):
        """What a session's window is marked with is tmux's way of keeping it, not a part of what
        is asked: the description and the requests' arguments name none."""
        self.assertNotIn("@", self.b.__doc__)
        for name, value in vars(self.b.Backend).items():
            if inspect.isfunction(value):
                self.assertFalse([p for p in inspect.signature(value).parameters if "option" in p], name)
        for backend in (self.b.Backend, self.b.Tmux):
            self.assertNotIn("@", inspect.getsource(backend.story), "a session's story is what it registered")

    def test_handoff_py_and_the_commands_reach_a_session_only_through_this_file(self):
        for path in ("scripts/handoff.py", "bin/flowkit-away", "bin/flowkit-tmux", "bin/flowkit-spawn",
                     "bin/flowkit-session"):
            found = re.findall(r'\[\s*"(?:tmux|claude)"|(?<![.\w])tmux\(|send-keys|capture-pane|list-panes|list-clients|'
                               r'show-options|@flowkit_|@cc_hold|"--bg"|"--resume"|new-window|split-window|list-windows|'
                               r'select-layout|list-sessions|display-message|set-option|claude --bg \$',
                               (PLUGIN / path).read_text())
            self.assertEqual(found, [], path)


class State(Case):
    """The file that keeps what flowkit knows of a session and no backend has to keep for it."""

    def sessions(self):
        return self.b.kept()["sessions"]

    def gone(self):
        """The pid of a process that has ended."""
        done = subprocess.Popen([sys.executable, "-c", "pass"])
        done.wait()
        return done.pid

    def test_a_session_registers_and_its_entry_is_read(self):
        self.assertEqual(self.b.kept(), {"sessions": {}, "places": {}}, "no file")
        entry = self.b.register(SID, "tmux", "%7", "my-story", pid=os.getpid())
        self.assertEqual(entry, {"backend": "tmux", "place": "%7", "story": "my-story", "adhoc": False, "pid": os.getpid(),
                                 "at": entry["at"]})
        self.assertAlmostEqual(entry["at"], time.time(), delta=5)
        self.assertEqual((self.b.registered("tmux", "%7"), self.b.registered("tmux", "%8"), self.b.registered("background", "%7")),
                         (entry, None, None))
        self.b.note(SID, rung=1700000000)
        again = self.b.register(SID, "tmux", "%9", "my-story", pid=os.getpid())
        self.assertEqual((again["place"], again["rung"]), ("%9", 1700000000), "a resume in another pane keeps what was noted")
        self.b.note("never-registered", rung=1700000300)
        self.assertEqual(self.sessions()["never-registered"]["rung"], 1700000300)
        self.b.keep(lambda doc: doc["places"].update({"tmux %3": {"parked": 1700000000}}))
        self.assertEqual(json.loads(self.b.STATE.read_text())["places"], {"tmux %3": {"parked": 1700000000}})
        self.assertEqual(sorted(p.name for p in self.b.STATE.parent.iterdir()), ["sessions.json", "sessions.lock"])

    def test_a_file_that_says_nothing_reads_as_empty(self):
        self.b.STATE.parent.mkdir(parents=True)
        for content in ("", "{", "[1]", '{"sessions": [], "places": {"tmux %1": 5}}'):
            self.b.STATE.write_text(content)
            self.assertEqual(self.b.kept(), {"sessions": {}, "places": {}}, content)
        self.assertEqual(self.b.Backend().story("%1"), "")

    def test_a_session_that_is_gone_leaves_its_place_then_the_file(self):
        now, gone = time.time(), self.gone()
        self.b.register("was-here", "tmux", "%7", "st", pid=gone)
        self.b.register("is-here", "tmux", "%7", "other", pid=os.getpid())
        self.assertEqual((list(self.sessions()), self.b.TMUX.story("%7")), (["is-here"], "other"))
        self.assertIsNone(self.b.register("run-from-inside-it", "tmux", "%7", "other", pid=gone, inside=[1, os.getpid()]),
                          "the pane is the session's that started this one: nothing is taken from it")
        self.b.register("cleared", "tmux", "%7", "other", pid=os.getpid())
        self.assertEqual(list(self.sessions()), ["cleared"], "a new id in the same process")
        self.b.register("is-here", "tmux", "%7", "other", pid=os.getpid())
        self.b.keep(lambda doc: doc["sessions"].update({
            "ended-long-ago": {"backend": "tmux", "place": "%1", "pid": gone, "at": now - 2 * self.b.GONE},
            "ended-today": {"backend": "tmux", "place": "%2", "pid": gone, "at": now - 3600},
            "still-running": {"backend": "tmux", "place": "%3", "pid": os.getpid(), "at": now - 2 * self.b.GONE},
            "noted-long-ago": {"rung": 5, "at": now - 2 * self.b.GONE}}))
        self.b.register("next", "tmux", "%4")
        self.assertEqual(sorted(self.sessions()), ["ended-today", "is-here", "next", "still-running"])

    def test_two_writers_at_once_lose_nothing(self):
        code = ("import importlib.util as u, sys\nfrom pathlib import Path\n"
                "s = u.spec_from_file_location('b', sys.argv[1]); b = u.module_from_spec(s); s.loader.exec_module(b)\n"
                "b.STATE = Path(sys.argv[2])\n"
                "for i in range(25):\n    b.note(f'{sys.argv[3]}-{i}', n=i)\n")
        writers = [subprocess.Popen([sys.executable, "-c", code, str(BACKEND), str(self.b.STATE), str(n)]) for n in range(4)]
        self.assertEqual([w.wait() for w in writers], [0] * 4)
        self.assertEqual(len(self.sessions()), 100)


class Launching(Case):
    """Where a launch goes and what it writes before its session runs: what the place was opened
    for and when, taken over by the session as it registers."""

    def setUp(self):
        super().setUp()
        self.t, self.g = self.b.TMUX, self.b.BACKGROUND
        self.repo = self.tmp / "repo"
        self.repo.mkdir()

    def asked(self, **more):
        return self.b.Launch(**{"story": "st", "name": "st-s2", "cwd": "/r", "args": "--name 'st-s2'", "target": "srv/main", **more})

    def record(self, pane="%7"):
        return self.b.kept()["places"].get(f"tmux {pane}")

    def panes(self, *rows):
        (self.tmp / "panes").write_text("".join(SEP.join(row) + "\n" for row in rows))

    def test_the_target_is_the_server_and_session_this_runs_in_or_the_one_a_caller_names(self):
        self.assertEqual(self.g.target(), "", "the background needs none")
        with self.assertRaises(self.b.Refused) as outside:
            self.t.target()
        self.assertEqual((outside.exception.why, str(outside.exception)), ("outside", "not inside tmux"))
        (self.tmp / "story").write_text("work\n")
        with mock.patch.dict(os.environ, {"TMUX": "/tmp/tmux-1/default,1,0", "TMUX_PANE": "%7"}):
            self.assertEqual(self.t.target(), "default/work")
            self.assertEqual(self.calls(), ["tmux display-message -p -t %7 #{session_name}"])
            with mock.patch.dict(os.environ, {"FLOWKIT_TARGET": "srv/main"}):
                self.assertEqual((self.t.target(), self.calls()), ("srv/main", []), "kept by a queued launch or a watch")
                self.assertIsNone(self.t.here(), "a pane of another server is not here: pane ids repeat")
                self.t.escape("%3")
                self.assertEqual(self.calls(), ["tmux -L srv send-keys -t %3 Escape"])
                with mock.patch.dict(os.environ, {"FLOWKIT_TMUX_SOCKET": "old", "FLOWKIT_TMUX_SESSION": "one"}):
                    self.assertEqual(self.t.target(), "old/one", "tmux's own names, as a launch of 0.15.0 was queued with")
        with mock.patch.dict(os.environ, {"FLOWKIT_TMUX_SOCKET": "srv"}), self.assertRaises(self.b.Refused) as nowhere:
            self.t.target()
        self.assertEqual((nowhere.exception.why, str(nowhere.exception)), ("nowhere", "no tmux session to launch in on server 'srv'"))

    def test_the_plan_is_an_idle_shell_a_free_slot_or_a_new_tab_of_the_story(self):
        tabs = "#{@flowkit_story_part} #{window_id} #{window_panes} #{@flowkit_story}"
        plan = self.t.plan(self.asked(reporter="st-report"), [])
        self.assertEqual((plan.tab, plan.pane, plan.part, plan.reporter), ("st", "new", 1, "in slot 1"))
        self.assertEqual(self.calls()[0], f"tmux list-windows -t main -F {tabs}")
        self.assertEqual(plan.show(self.asked()), [("tab", "new tab st after the last one"), ("pane", "<new, tiled after the last pane>"),
                                                   ("send", "cd '/r' && clear; claude --name 'st-s2'")])
        (self.tmp / "windows").write_text("2 @4 3 st\n1 @3 9 st\n1 @9 1 other\n @1 1\n")
        (self.tmp / "panes").write_text("%4 2.1.328 0 0\n%5 zsh 0 1\n%6 zsh 0 0\n")
        plan = self.t.plan(self.asked(reporter="st-report"), [{"name": "st-report"}])
        self.assertEqual((plan.tab, plan.pane, plan.win, plan.reporter), ("", "%6", "@3", ""), "the first idle shell, by part")
        (self.tmp / "panes").write_text("%4 2.1.328 0 0\n")
        plan = self.t.plan(self.asked(), [])
        self.assertEqual((plan.pane, plan.win), ("new", "@4"), "part 1 is full: a slot in part 2")
        (self.tmp / "windows").write_text("2 @4 9 st\n1 @3 9 st\n")
        plan = self.t.plan(self.asked(), [])
        self.assertEqual((plan.tab, plan.pane, plan.part), ("st-3", "new", 3))
        (self.tmp / "story").write_text("vim\n")
        with self.assertRaises(self.b.Refused) as busy:
            self.t.plan(self.asked(place="%9"), [])
        self.assertEqual((busy.exception.why, str(busy.exception)), ("busy", "pane %9 is running 'vim', not an idle shell"))

    def test_a_session_of_that_name_runs_where_claude_lists_it_or_a_pane_shows_it(self):
        row = ["900", "%1", "work", "@1", "1", "w", "1", "2.1.328", "", "", "", "", "", "", "✳ st-s2"]
        self.panes(row, [*row[:1], "%2", *row[2:7], "zsh", *row[8:14], "st-s3"])
        self.assertEqual(self.b.running(self.t, "st-s2", [{"name": "st-s2"}]), "claude agents")
        self.assertEqual(self.b.running(self.t, "st-s2", []), "%1", "not listed yet: its pane's title")
        self.assertEqual(self.b.running(self.t, "st-s3", []), "", "a shell that kept a title runs nothing")
        with mock.patch.dict(os.environ, {"TMUX_PANE": "%1"}):
            self.assertEqual(self.b.running(self.t, "st-s2", []), "", "never the pane this runs in")
        self.assertEqual(self.b.running(self.g, "st-s2", []), "")

    def test_a_launch_writes_what_the_pane_was_opened_for_before_it_types(self):
        self.t.typed("%7", "--name 'st-s2'", self.asked())
        record = self.record()
        self.assertEqual((record["story"], record["adhoc"]), ("st", False))
        self.assertAlmostEqual(record["born"], time.time(), delta=5)
        self.assertEqual(self.calls()[-2:], ["tmux send-keys -t %7 C-u",
                                             "tmux send-keys -t %7 cd '/r' && clear; claude --name 'st-s2' Enter"])
        self.assertEqual((self.t.story("%7"), self.calls()), ("st", []), "the file answers, tmux is not asked")
        self.b.keep(lambda doc: doc["places"]["tmux %7"].update(parked=5))
        self.t.typed("%7", "--name 'look'", self.asked(story="adhoc", adhoc=True))
        self.assertEqual({k: v for k, v in self.record().items() if k != "born"}, {"story": "", "adhoc": True},
                         "a one-off has no story, and the dialog seen for the pane's last session is not this one's")

    def test_the_session_takes_the_record_over_as_it_registers(self):
        self.b.launched("tmux", "%7", "st")
        born = self.record()["born"]
        entry = self.b.register(SID, "tmux", "%7", "what-its-tab-says", pid=os.getpid())
        self.assertEqual((entry["story"], entry["adhoc"], entry["born"]), ("st", False, born))
        self.assertIsNone(self.record(), "the record is the session's now")
        self.assertEqual(self.b.register(SID, "tmux", "%7", "st", pid=os.getpid())["born"], born, "a resume keeps its launch stamp")
        self.b.launched("tmux", "%8", "adhoc", True)
        entry = self.b.register("one-off", "tmux", "%8", "", pid=os.getpid())
        self.assertEqual((entry["story"], entry["adhoc"]), ("", True))

    def test_a_launch_whose_session_never_came_up_still_says_whose_pane_it_is(self):
        self.panes(["900", "%1", "work", "@1", "1", "w", "1", "zsh", "", "", "", "", "", "", "zsh"])
        self.b.launched("tmux", "%1", "st")
        pane = self.t.places()[0]
        self.assertEqual((pane["story"], pane["slug"], pane["title"]), ("st", "st", "zsh"), "its tab has no mark at all")
        self.assertAlmostEqual(int(pane["born"]), time.time(), delta=5)
        self.b.launched("tmux", "%1", "adhoc", True)
        pane = self.t.places()[0]
        self.assertEqual((pane["story"], pane["slug"]), ("adhoc", ""))

    def test_a_pane_used_again_is_the_new_launchs(self):
        self.panes(["900", "%7", "work", "@1", "1", "w", "1", "zsh", "old", "old", "", "100", "", "", ""])
        self.b.register("left", "tmux", "%7", "old", pid=os.getpid())
        self.b.keep(lambda doc: doc["sessions"]["left"].update(born=200))
        self.assertEqual(self.t.places()[0]["born"], "200", "the stamp its session took over")
        self.b.launched("tmux", "%7", "new")
        pane = self.t.places()[0]
        self.assertEqual((self.t.story("%7"), pane["story"], pane["slug"]), ("new", "new", "new"))
        self.assertEqual(int(pane["born"]), self.record()["born"])
        entry = self.b.register(SID, "tmux", "%7", "old", pid=os.getpid())
        self.assertEqual((entry["story"], list(self.b.kept()["sessions"])), ("new", [SID]), "the session that left is gone")

    def test_a_tab_an_older_launch_marked_is_read_from_tmux(self):
        row = ["900", "%1", "work", "@1", "1", "my-story", "2", "zsh", "my-story", "my-story", "", "1700000000", "", ""]
        self.panes(row)
        pane = self.t.places()[0]
        self.assertEqual((pane["story"], pane["slug"], pane["born"]), ("my-story", "my-story", "1700000000"))
        (self.tmp / "story").write_text(f"my-story{SEP}\n")
        self.assertEqual(self.t.story("%1"), "my-story")
        self.b.register(SID, "tmux", "%1", *self.t.marked("%1"), pid=os.getpid())
        self.assertEqual(self.b.kept()["sessions"][SID]["story"], "my-story", "a session starting there takes the tab's")

    def bg(self, said="backgrounded · abc123", code=0):
        d = self.tmp
        self.stub("claude", f'case "$1" in\nagents) cat {d}/agents.json;;\n'
                            f'--bg) echo "claude $* (in $(pwd -P))" >> {d}/calls; echo "{said}"; exit {code};;\n'
                            f'logs) echo "connect: https://claude.ai/code/session_AB12 (open)";;\nesac\nexit 0\n')

    def test_a_background_launch_writes_the_story_under_the_session_s_id(self):
        self.bg()
        self.agents([{"id": "abc123", "sessionId": SID, "pid": os.getpid(), "kind": "background", "name": "st-s2"}])
        asked, said = self.asked(cwd=str(self.repo), reporter="st-report"), []
        plan = self.g.plan(asked, [])
        self.assertEqual((plan.reporter, plan.show(asked)),
                         ("first", [("driver", "background"), ("run", f"cd '{self.repo}' && claude --bg --name 'st-s2'")]))
        asked.reporter_args = "--name 'st-report'"
        opened = list(self.g.open(asked, plan, said.append))
        link = "https://claude.ai/code/session_AB12"
        self.assertEqual([(o.name, o.where, o.up, o.rc, o.tried) for o in opened],
                         [("st-report", "driver=background id=abc123", True, link, 0),
                          ("st-s2", "driver=background id=abc123", True, link, 0)])
        self.assertEqual((said, self.calls()), ([], [f"claude --bg --name st-report (in {self.repo.resolve()})",
                                                     f"claude --bg --name st-s2 (in {self.repo.resolve()})"]))
        entry = self.b.kept()["sessions"][SID]
        self.assertEqual((entry["backend"], entry["place"], entry["story"], entry["adhoc"]), ("background", SID, "st", False))
        self.assertEqual(self.g.story(self.b.Place(SID, False, None)), "st")
        self.agents([{"id": "abc123", "name": "st-report"}])
        self.assertEqual(self.g.plan(asked, []).reporter, "", "a stopped reporter is woken, not started again")

    def test_a_background_launch_that_is_not_listed_or_refused_says_so(self):
        self.bg()
        said = []
        (opened,) = self.g.open(self.asked(cwd=str(self.repo), rc=False), self.g.plan(self.asked(), []), said.append)
        self.assertEqual((opened.up, opened.rc, self.b.kept()["sessions"]), (False, "off", {}))
        self.assertEqual(said, ["warning: background session 'st-s2' (abc123) is not listed by claude agents after 15s; "
                                "check claude logs abc123"])
        for stub, why, text in ((("workspace not trusted", 1), "untrusted", "workspace not trusted"),
                                (("boom", 1), "start", "claude --bg failed:\nboom"),
                                (("started", 0), "start", "claude --bg printed no session id:\nstarted")):
            self.bg(*stub)
            with self.assertRaises(self.b.Refused) as refused:
                list(self.g.open(self.asked(cwd=str(self.repo)), self.g.plan(self.asked(), []), said.append))
            self.assertEqual((refused.exception.why, str(refused.exception)), (why, text))


class OutsideDriver(Case):
    """The same requests, asked of an executable of the user's own, `flowkit-driver-stub`: it
    answers open, list, type, screen, label and close from files and has no answer to pick. Its
    calls are recorded with Enter, Escape and Ctrl-U as R, E and U."""

    def setUp(self):
        super().setUp()
        d = self.tmp
        self.stub("flowkit-driver-stub", f"printf '%s\\n' \"driver $*\" | tr '\\015\\033\\025' 'REU' >> {d}/calls\n"
                                         f"grep -qx \"$1\" {d}/silent && exit 2\n"
                                         f"[ -f {d}/refuse ] && {{ echo 'no room' >&2; exit 1; }}\n"
                                         f"case \"$1\" in\nopen) cat {d}/opened;;\nlist) cat {d}/places;;\n"
                                         f"screen) cat {d}/screen;;\nesac\nexit 0\n")
        (d / "silent").write_text("pick\n")
        (d / "places").write_text("p1\t500\np2\t\n")
        (d / "opened").write_text("p9\n")
        (d / "ps").write_text(f"700 600\n600 500\n{os.getpid()} 500\n")
        self.b.CONF = d / "handoff.conf"
        self.b.CONF.write_text("reporter=on\ndriver=stub\n")
        self.s = self.b.driver()

    def asked(self, **more):
        return self.b.Launch(**{"story": "st", "name": "st-s2", "cwd": str(self.tmp), "args": "--name 'st-s2'", **more})

    def leaves_out(self, *requests):
        (self.tmp / "silent").write_text("".join(f"{r}\n" for r in requests))

    def test_the_driver_is_the_one_handoff_conf_names(self):
        path = str(self.tmp / "bin" / "flowkit-driver-stub")
        self.assertIsInstance(self.s, self.b.Driver)
        self.assertEqual((self.s.name, self.s.program, self.s.path, self.s.available()), ("stub", "flowkit-driver-stub", path, True))
        self.assertIs(self.b.driver("stub"), self.s)
        self.assertIs(self.b.terminal(), self.s, "a session in a terminal runs in the driver's places")
        self.assertEqual((self.b.driver("tmux"), self.b.driver("background")), (self.b.TMUX, self.b.BACKGROUND))
        for line, named in (("driver=tmux", self.b.TMUX), ("driver=background", self.b.BACKGROUND), ("", self.b.TMUX),
                            ("driver=nope", None), ("driver=../bin/flowkit-driver-stub", None), ("driver = stub ", self.s)):
            self.b.CONF.write_text(f"{line}\n")
            self.assertIs(self.b.driver(), named, line)
            self.assertIs(self.b.terminal(), self.s if named is self.s else self.b.TMUX, line)
        self.b.CONF.unlink()
        self.assertEqual((self.b.configured(), self.b.driver()), ("tmux", self.b.TMUX))
        self.assertEqual(self.calls(), [], "naming a driver asks it nothing")
        self.without("flowkit-driver-stub")
        self.assertEqual((self.b.driver("stub"), self.s.available()), (None, False))

    def test_it_answers_the_requests_of_the_other_two(self):
        for name, value in vars(self.b.Backend).items():
            if inspect.isfunction(value):
                self.assertEqual(inspect.signature(getattr(self.s, name)), inspect.signature(getattr(self.b.Backend(), name)), name)
        self.assertEqual((self.s.target(), self.s.last_input(), self.s.marked("p1")), ("", None, ("", False)))

    def test_a_session_is_found_through_the_processes_above_it_up_to_the_place_s_own(self):
        self.assertEqual(self.s.locate(pid=700), self.b.Place("p1", False, None))
        self.assertEqual((self.s.locate(pid=999), self.s.locate(sid=SID), self.s.here()), (None, None, self.b.Place("p1", False, None)))
        self.calls()
        find = self.s.finder()
        self.assertEqual(self.calls(), ["driver list"])
        self.assertEqual((find(700).id, find(600).id, find(1), self.calls()), ("p1", "p1", None, []))
        self.leaves_out("list")
        self.assertEqual((self.s.locate(pid=700), self.s.places()), (None, []), "a driver with no list has no place for anyone")

    def test_places_are_the_driver_s_list_with_what_the_file_keeps_for_each(self):
        blank = {"sess": "", "widx": "", "pidx": "", "cmd": "", "hold": "", "rung": "", "parked": "", "title": ""}
        self.assertEqual(self.s.places(), [
            {"id": "p1", "pid": "500", "wid": "p1", "wname": "-", "story": "", "slug": "", "born": "", **blank},
            {"id": "p2", "pid": "", "wid": "p2", "wname": "-", "story": "", "slug": "", "born": "", **blank}])
        self.b.launched("stub", "p1", "st")
        self.b.launched("stub", "p2", "adhoc", adhoc=True)
        one, two = self.s.places()
        self.assertEqual((one["story"], one["slug"], one["wname"], one["born"].isdigit()), ("st", "st", "st", True))
        self.assertEqual((two["story"], two["slug"], self.s.story("p1"), self.s.story("p2")), ("adhoc", "", "st", ""))
        entry = self.b.register(SID, "stub", "p1", pid=700)
        self.assertEqual((entry["story"], self.b.kept()["places"].get("stub p1")), ("st", None), "the session took the record over")
        self.assertEqual((self.s.story(self.b.Place("p1", False, None)), self.s.places()[0]["slug"]), ("st", "st"))
        panes = dict(zip(("pid", "id", "sess", "wid", "widx", "wname", "pidx", "cmd", "story", "slug", "hold", "born", "rung",
                          "parked", "title"), range(15)))
        self.assertEqual(set(one), set(panes), "the keys of a tmux pane")

    def test_a_line_is_its_characters_then_enter_after_the_pause(self):
        self.assertEqual(self.s.type("p1", "hello"), "")
        self.assertEqual((self.calls(), self.slept), (["driver type p1 hello", "driver type p1 R"], [self.b.PAUSE]))
        self.assertEqual(self.s.type(self.b.Place("p1", False, None), "hello", self.b.OVER), "")
        self.assertEqual(self.calls(), ["driver type p1 Uhello", "driver type p1 R"])
        self.assertEqual(self.s.type("p1", "hello", self.b.ESCAPE, settled=lambda: True), "")
        self.assertEqual(self.calls(), ["driver type p1 E", "driver type p1 Uhello", "driver type p1 R"])
        self.assertEqual(self.s.type("p1", "hello", self.b.ESCAPE, settled=lambda: False), "not settled")
        self.assertEqual((self.calls(), self.s.escape("p1"), self.calls()), (["driver type p1 E"], True, ["driver type p1 E"]))
        (self.tmp / "refuse").write_text("")
        self.assertEqual((self.s.type("p1", "hello"), self.s.escape("p1")), ("not typed", False))

    def test_a_line_for_a_free_box_looks_at_the_screen_the_driver_shows(self):
        for screen, why, draft in (("❯ \n", "", False), ("❯ half typed\n", "draft", True), ("no box here\n", "no prompt box", False),
                                   ("❯ \n Enter to confirm · Esc to cancel\n", "dialog", False)):
            (self.tmp / "screen").write_text(f"above\n{screen}")
            self.assertEqual((self.s.parked("p1"), self.s.draft("p1")), (why, draft), screen)
            self.calls()
            self.assertEqual(self.s.type("p1", "/cmd", self.b.COMMAND), why)
            self.assertEqual(self.calls(), ["driver screen p1"] + ([] if why else ["driver type p1 /cmd", "driver type p1 R"]))
        self.assertEqual((self.s.screen("p1")[0], self.s.bottom("p1")), (("above", "above"), ["above", "❯ ", " Enter to confirm · Esc to cancel"]))
        self.leaves_out("screen", "pick")
        self.calls()
        self.assertEqual((self.s.screen("p1"), self.s.bottom("p1"), self.s.parked("p1"), self.s.draft("p1")), ([], [], "", False))
        self.assertEqual(self.s.type("p1", "ring", self.b.FREE), "", "with no screen to look at the line is typed")
        self.assertEqual(self.calls(), ["driver screen p1", "driver type p1 ring", "driver type p1 R"], "what it left out is asked once")

    def test_label_close_and_pick(self):
        self.assertIsNone(self.s.label({"p1": "waiting"}, {"p1": (1, "needs you")}))
        self.assertEqual(self.calls(), ["driver list", "driver label p1 waiting", "driver label p2 "])
        self.assertEqual((self.s.close("p1"), self.s.pick("p1"), self.s.pick("p2", "client")), (True, False, False))
        self.assertEqual(self.calls(), ["driver close p1", "driver pick p1"], "a request it has no answer to is asked once")
        (self.tmp / "refuse").write_text("")
        self.assertEqual((self.s.label({}, {}), self.s.close("p1")), (False, False))

    def test_a_launch_opens_a_place_and_waits_until_claude_lists_the_session_under_it(self):
        (self.tmp / "places").write_text("p9\t500\n")
        (self.tmp / "screen").write_text("connect: https://claude.ai/code/session_AB12 (open)\n")
        self.agents([{"name": "st-s2", "pid": 700}, {"name": "st-report", "pid": 600}])
        asked, said = self.asked(reporter="st-report", reporter_args="--name 'st-report'"), []
        plan = self.s.plan(asked, [])
        run = f"flowkit-driver-stub open st st-s2 {self.tmp} " + "'claude --name '\"'\"'st-s2'\"'\"''"
        self.assertEqual((plan.reporter, plan.show(asked)), ("first", [("driver", "stub"), ("run", run)]))
        self.assertEqual(self.s.plan(asked, [{"name": "st-report"}]).reporter, "", "a story's reporter that runs is not started again")
        self.calls()
        opened = list(self.s.open(asked, plan, said.append))
        link = "https://claude.ai/code/session_AB12"
        self.assertEqual([(o.name, o.place, o.where, o.up, o.rc, o.created, o.tried, o.parked) for o in opened],
                         [("st-report", "p9", "driver=stub id=p9", True, link, 1, 1, ""),
                          ("st-s2", "p9", "driver=stub id=p9", True, link, 1, 1, "")])
        calls = self.calls()
        self.assertEqual([c for c in calls if c.startswith("driver open")],
                         [f"driver open st st-report {self.tmp} claude --name 'st-report'",
                          f"driver open st st-s2 {self.tmp} claude --name 'st-s2'"])
        self.assertEqual((said, self.slept, self.b.kept()["places"]["stub p9"]["story"]), ([], [1, 1], "st"))
        self.agents([{"name": "st-s2", "pid": 999}])
        (opened,) = self.s.open(self.asked(rc=False), self.s.plan(self.asked(), []), said.append)
        self.assertEqual((opened.up, opened.rc, len(said)), (False, "off", 1), "a namesake that runs elsewhere is not the session")

    def test_a_driver_that_only_opens_lists_and_types_still_launches(self):
        self.leaves_out("screen", "label", "close", "pick")
        (self.tmp / "places").write_text("p9\t500\n")
        self.agents([{"name": "st-s2", "pid": 700}])
        (opened,) = self.s.open(self.asked(), self.s.plan(self.asked(), []))
        self.assertEqual((opened.up, opened.rc, self.slept), (True, "pending", [1]), "no screen to read a link from: none is waited for")

    def test_a_place_that_is_gone_at_once_is_opened_once_more_then_refused(self):
        (self.tmp / "places").write_text("")
        said, again = [], []
        with self.assertRaises(self.b.Refused) as refused:
            list(self.s.open(self.asked(), self.s.plan(self.asked(), []), said.append, again.append))
        self.assertEqual((refused.exception.why, str(refused.exception)),
                         ("exited", "session 'st-s2' exited twice at start-up; flowkit-driver-stub kept no place for it"))
        self.assertEqual((said, again, self.slept), (["warning: session 'st-s2' exited at start-up in p9; opened again"],
                                                     ["st-s2"], [1] * 6))
        self.assertEqual(len([c for c in self.calls() if c.startswith("driver open")]), 2)

    def test_a_place_that_stays_with_no_session_listed_gets_the_warning(self):
        (self.tmp / "places").write_text("p9\t500\n")
        said = []
        (opened,) = self.s.open(self.asked(), self.s.plan(self.asked(), []), said.append, None, lambda screen: "Do you trust this folder?")
        self.assertEqual((opened.up, opened.tried, opened.parked, len(self.slept)), (False, 1, "Do you trust this folder?", 15))
        (opened,) = self.s.open(self.asked(), self.s.plan(self.asked(), []), said.append, None, lambda screen: "")
        self.assertEqual(said, ["warning: session 'st-s2' is parked at a start-up prompt in p9 (Do you trust this folder?); answer it there",
                                "warning: no session 'st-s2' came up in p9 within 15s; look at it (a folder trust prompt?)"])

    def test_a_driver_that_opens_nothing_is_a_launch_refused(self):
        for setup, text in ((lambda: (self.tmp / "opened").write_text(""), "flowkit-driver-stub open gave no place"),
                            (lambda: (self.tmp / "refuse").write_text(""), "no room"),
                            (lambda: ((self.tmp / "refuse").unlink(), self.leaves_out("open")), "flowkit-driver-stub open gave no place")):
            setup()
            with self.assertRaises(self.b.Refused) as refused:
                list(self.s.open(self.asked(), self.s.plan(self.asked(), [])))
            self.assertEqual((refused.exception.why, str(refused.exception)), ("failed", text))
        self.assertEqual(self.b.kept()["places"], {}, "nothing was opened, nothing is written")


if __name__ == "__main__":
    unittest.main()
