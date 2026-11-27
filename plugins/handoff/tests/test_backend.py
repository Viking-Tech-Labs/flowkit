"""Run with `python3 -m unittest discover plugins/handoff/tests`. backend.py loaded by its path, as
a command copied out of the plugin loads it, with a `tmux`, a `ps` and a `claude` of the test's own
that answer from files and record what they were called with: every request, asked of the tmux
backend and of the background one. No pause is waited for: the module's `time.sleep` only records."""
import importlib.util
import inspect
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parent.parent
BACKEND = PLUGIN / "scripts" / "backend.py"
PANES = "tmux list-panes -a -F #{pane_pid} #{pane_id} #{pane_in_mode}"
RULE = "─" * 20
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
                          f"list-panes) cat {d}/panes;;\ncapture-pane) cat {d}/screen;;\nshow-options) cat {d}/story;;\n"
                          f"list-clients) cat {d}/clients;;\nsend-keys) [ -f {d}/refuse ] && exit 1;;\nesac\nexit 0\n")
        self.stub("ps", f"cat {d}/ps\n")
        self.stub("claude", f'case "$1" in\nagents) echo "claude $*" >> {d}/calls; cat {d}/agents.json;;\n'
                            f'stop) echo "claude $*" >> {d}/calls; [ -f {d}/stopped.json ] && mv {d}/stopped.json {d}/agents.json;;\n'
                            f'*) echo "claude $* (in $(pwd -P))" >> {d}/calls; [ -f {d}/refuse ] && exit 1;;\nesac\nexit 0\n')
        for name in ("panes", "screen", "story", "clients", "ps"):
            (d / name).write_text("")
        self.agents([])
        self.env = {"PATH": f"{d / 'bin'}:/usr/bin:/bin"}
        patch = mock.patch.dict(os.environ, self.env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        self.slept = []
        self.b = load()
        self.b.time = mock.Mock(sleep=self.slept.append)  # its own pauses alone: subprocess sleeps while it waits, too

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
        line = "tmux send-keys -t %7 -l carry on ; run-shell sleep 0.5 ; send-keys -t %7 Enter"
        self.assertEqual(self.calls(), [line, line])
        self.assertEqual(self.t.type(self.pane, "closed s1", self.b.OVER), "")
        self.assertEqual(self.calls(), ["tmux send-keys -t %7 C-u ; send-keys -t %7 -l closed s1 ; run-shell sleep 0.5 ; "
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
            self.assertEqual(self.calls(), ["tmux capture-pane -p -e -t %7", "tmux send-keys -t %7 -l /remote-control ; "
                                            "run-shell sleep 0.5 ; send-keys -t %7 Enter"])
            (self.tmp / "refuse").write_text("")
            self.assertEqual(self.t.type(self.pane, "/remote-control", how), "not typed")
            (self.tmp / "refuse").unlink()
            self.calls()

    def test_escape_first_ends_the_question_then_types_over_what_is_there(self):
        asked = []
        self.assertEqual(self.t.type(self.pane, "away: carry on", self.b.ESCAPE, settled=lambda: asked.append(1) or True), "")
        self.assertEqual(self.calls(), ["tmux send-keys -t %7 Escape", "tmux send-keys -t %7 C-u ; send-keys -t %7 -l "
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
        (self.tmp / "story").write_text("my-story\n")
        self.assertEqual(self.t.story(self.pane), "my-story")
        self.assertEqual(self.calls()[-1], "tmux show-options -wqv -t %7 @flowkit_story")
        (self.tmp / "story").write_text("")
        self.assertEqual(self.t.story("%7"), "")
        self.assertIsNone(self.t.last_input(), "no client attached")
        (self.tmp / "clients").write_text("1700000000\n1700000300\n\n")
        self.assertEqual(self.t.last_input(), 1700000300)


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
                          self.g.escape(place), self.g.story(place), self.g.last_input()),
                         (None, [], [], "", False, False, "", None))
        self.assertEqual(self.calls(), [])

    def test_a_line_resumes_a_stopped_session_under_its_own_id(self):
        place = self.place()
        for how in (self.b.LINE, self.b.OVER, self.b.FREE, self.b.ESCAPE):
            self.assertEqual(self.g.type(place, "closed s1", how), "", how)
            self.assertEqual(self.calls(), [self.resume("closed s1")], "no process to stop")
        self.assertEqual(self.slept, [])
        (self.tmp / "refuse").write_text("")
        self.assertEqual(self.g.type(place, "closed s1"), "not typed")

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
        self.assertEqual(requests, ["available", "bottom", "draft", "escape", "here", "last_input", "locate", "parked",
                                    "screen", "story", "type"])
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


if __name__ == "__main__":
    unittest.main()
