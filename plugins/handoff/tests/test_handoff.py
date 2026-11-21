"""Run with `python3 -m unittest discover plugins/handoff/tests`. Each test gets its own config
dir and temp dir, a stub `claude` that serves a session list and a usage answer and records what
else it was called with, and a stub `tmux` that serves a pane list, so nothing reaches a real
session or the user's files."""
import contextlib
import fcntl
import importlib.machinery
import importlib.util
import io
import json
import os
import re
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

PLUGIN = Path(__file__).resolve().parent.parent
HANDOFF = PLUGIN / "scripts" / "handoff.py"
AWAY_BIN = PLUGIN / "bin" / "flowkit-away"
SPAWN_BIN = PLUGIN / "bin" / "flowkit-spawn"


class Handoff(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "bin").mkdir()
        (self.tmp / "claude").mkdir()
        self.calls = self.tmp / "calls"
        self.flag = self.tmp / "claude" / "away"
        self.env = {**os.environ, "CLAUDE_CONFIG_DIR": str(self.tmp / "claude"), "TMPDIR": str(self.tmp),
                    "PATH": f"{self.tmp / 'bin'}:{os.environ['PATH']}"}
        # the Chrome question was answered once, as on a machine in use: a launch asks claude nothing about it
        (self.tmp / "claude" / ".claude.json").write_text('{"claudeInChromeDefaultEnabled": false}')
        self.agents([])
        self.panes("")
        self.addCleanup(lambda: self.flag.unlink(missing_ok=True))  # the waiter a test's `away` started ends with it

    def agents(self, rows):
        (self.tmp / "agents.json").write_text(json.dumps(rows))
        stub = self.tmp / "bin" / "claude"
        stub.write_text(f"#!/bin/sh\nif [ \"$1\" = agents ]; then cat {self.tmp / 'agents.json'}; "
                        f"elif [ \"$1\" = -p ]; then echo probe >> {self.tmp / 'probes'}; "
                        f"cat {self.tmp / 'usage.jsonl'}; "
                        f"else echo \"$@\" >> {self.calls}; fi\n")
        stub.chmod(0o755)

    def panes(self, text):
        stub = self.tmp / "bin" / "tmux"
        stub.write_text(f"#!/bin/sh\nif [ \"$1\" = list-panes ]; then printf '%s\\n' '{text}'; "
                        f"else echo tmux \"$@\" >> {self.calls}; fi\n")
        stub.chmod(0o755)

    def handoff(self, *args, stdin=""):
        return subprocess.run(["python3", str(HANDOFF), *args], input=stdin, capture_output=True,
                              text=True, env=self.env)

    def away(self, *args):
        return subprocess.run(["python3", str(AWAY_BIN), *args], capture_output=True, text=True, env=self.env)

    def gate(self, sid=None):
        data = {"tool_name": "AskUserQuestion", **({"session_id": sid} if sid else {})}
        out = self.handoff("question-gate", stdin=json.dumps(data)).stdout
        return json.loads(out)["hookSpecificOutput"] if out else None

    def set_flag(self, at, by=None, written=None):
        """The away flag taking effect at epoch `at` (by `away --by <by>`; without: written raw, its
        stamp alone), its file written at `written` (default now)."""
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(at))
        self.flag.write_text(f"{stamp}\nby: {by}\nset: {stamp}\n" if by else f"{stamp}\n")
        if written is not None:
            os.utime(self.flag, (written, written))
        return stamp

    def test_question_gate_follows_the_flag(self):
        self.assertIsNone(self.gate())
        self.flag.write_text("2026-10-18 23:10\n")
        out = self.gate()
        self.assertEqual(out["permissionDecision"], "deny")
        self.assertIn("since 2026-10-18 23:10", out["permissionDecisionReason"])
        self.assertIn('"(auto)"', out["permissionDecisionReason"])
        old = time.time() - 13 * 3600
        os.utime(self.flag, (old, old))
        self.assertIsNone(self.gate())

    def test_state_prints_the_chain_end_of_the_conf(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        self.env["XDG_CONFIG_HOME"] = str(self.tmp)
        conf = self.tmp / "flowkit" / "handoff.conf"
        conf.parent.mkdir()
        state = lambda: self.handoff("state", str(self.tmp / "data"), str(repo)).stdout
        self.assertIn("chain-end: mr-ready (each slice pushes its green commits, the first push opens a Draft MR; "
                      "the last slice pushes, registers the watch and closes: the watch takes the MR out of Draft on "
                      "green, a red pipeline gets one fix slice, no merge)", state())
        conf.write_text("driver=tmux\nchain_end=merge\n")
        self.assertIn("chain-end: merge (each slice pushes", state())
        conf.write_text("chain_end=ship\n")
        self.assertIn("chain-end: ship (not one of branch, draft-mr, mr-ready, merge)", state())

    def test_state_prints_the_report_to_of_the_conf(self):
        repo = self.tmp / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        self.env["XDG_CONFIG_HOME"] = str(self.tmp)
        conf = self.tmp / "flowkit" / "handoff.conf"
        conf.parent.mkdir()
        state = lambda: self.handoff("state", str(self.tmp / "data"), str(repo)).stdout
        self.assertIn("report-to: off\n", state())
        conf.write_text("driver=tmux\nreport_to=on\n")
        self.assertIn("report-to: on\n", state())
        conf.write_text("report_to=billing-hub\n")
        self.assertIn("report-to: billing-hub\n", state())

    def test_self_reads_the_sessions_file_then_the_agents_list(self):
        sessions = self.tmp / "claude" / "sessions"
        sessions.mkdir()
        (sessions / "4242.json").write_text(json.dumps({"pid": 4242, "sessionId": "sid-1", "name": "billing-s2"}))
        self.env["CLAUDE_PID"] = "4242"
        self.assertEqual(self.handoff("self").stdout, "name: billing-s2\nid: -\nsession: sid-1\n")
        self.env["CLAUDE_PID"] = "4243"
        out = self.handoff("self")
        self.assertEqual(out.returncode, 4)
        self.assertIn("name it with /rename", out.stderr)
        self.agents([{"kind": "interactive", "name": "billing-hub", "pid": os.getpid(), "sessionId": "sid-2"}])
        self.assertEqual(self.handoff("self").stdout, "name: billing-hub\nid: -\nsession: sid-2\n",
                         "the agents row of a parent pid, when the sessions file has no name")

    def test_save_prompt_puts_the_report_to_line_in_the_header(self):
        store = self.tmp / "claude" / "spawned"
        brief = "Repo: /r   Session: s2   Story: billing\nModel: opus\n\nToday: review it\n"
        out = self.handoff("save-prompt", "s2", "--report-to", "billing-hub", stdin=brief)
        self.assertEqual(out.stdout, f"{store / 's2.md'}\n")
        with_line = "Repo: /r   Session: s2   Story: billing   Report-to: billing-hub\nModel: opus\n\nToday: review it\n"
        self.assertEqual((store / "s2.md").read_text(), with_line)
        self.handoff("save-prompt", "s3", "--report-to", "billing-hub", stdin="review the branch\n")
        self.assertEqual((store / "s3.md").read_text(), "Report-to: billing-hub\n\nreview the branch\n")
        for text in (with_line, 'Task\n\nREPORTING: SendMessage to "hub" when DONE\n'):
            self.handoff("save-prompt", "s4", "--report-to", "other", stdin=text)
            self.assertEqual((store / "s4.md").read_text(), text, "a prompt that already reports is left as it is")
        self.handoff("save-prompt", "s5", stdin=brief)
        self.assertEqual((store / "s5.md").read_text(), brief, "without the option, nothing is added")

    def test_save_prompt_keeps_a_command_prompt_to_its_one_line(self):
        store = self.tmp / "claude" / "spawned"
        out = self.handoff("save-prompt", "s2", "--report-to", "billing-hub", stdin="/implement-story 1526354\n")
        self.assertEqual((out.returncode, out.stdout), (0, f"{store / 's2.md'}\n"))
        self.assertEqual((store / "s2.md").read_text(), "/implement-story 1526354\n", "no Report-to line in a command")
        self.assertIn("Report-to: billing-hub into the brief", out.stderr)
        out = self.handoff("save-prompt", "s3", stdin="/implement-story 1526354\n\nRepo: /r   Session: s3\n")
        self.assertNotEqual(out.returncode, 0, "a brief after the command would reach it as arguments")
        self.assertFalse((store / "s3.md").exists())

    def test_chain_end_lives_in_the_story_header(self):
        story = self.tmp / "claude" / "stories" / "my-story.md"
        self.assertEqual(self.handoff("chain-end", "My Story").stdout, "none\n")
        self.assertFalse(story.exists(), "reading writes nothing")
        line = self.handoff("chain-end", "My Story", "mr-ready").stdout.strip()
        self.assertTrue(line.startswith("Chain end: mr-ready — each slice pushes its green commits"), line)
        self.assertEqual(story.read_text(), f"# my-story\n{line}\n")
        self.assertEqual(self.handoff("chain-end", "my-story").stdout.strip(), line[len("Chain end: "):])
        self.handoff("record", "my-story", stdin="## s1 · 2026-10-27 12:00\nSession: x · Repo: /r\nNext: none\n")
        self.handoff("chain-end", "my-story", "push, MR ready,\nno merge")
        text = story.read_text()
        self.assertEqual(text.splitlines()[:2], ["# my-story", "Chain end: push, MR ready, no merge"])
        self.assertEqual(text.count("Chain end:"), 1, "the header line is replaced, never doubled")
        self.assertIn("\n## s1 · 2026-10-27 12:00\nSession: x · Repo: /r\nNext: none\n", text)
        self.assertEqual(self.handoff("chain-end", "my-story", "   ").returncode, 1)

    def test_record_wants_a_close_heading_then_rings_in_one_call(self):
        story = self.tmp / "claude" / "stories" / "my-story.md"
        out = self.handoff("record", "my-story", stdin="closed without a heading\nDONE\n- x\n")
        self.assertEqual(out.returncode, 1)
        self.assertIn("## <session name> · YYYY-MM-DD HH:MM", out.stderr)
        self.assertFalse(story.exists(), "a refused record writes nothing")
        self.agents([{"kind": "interactive", "name": "my-story-report", "pid": os.getpid(), "status": "idle"}])
        self.panes(f"{os.getppid()} %7")
        out = self.handoff("record", "my-story", stdin="## s1 · 2026-10-27 12:00\nSession: x · Repo: /r\nNext: none\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(story.read_text().startswith("# my-story\n\n## s1 · 2026-10-27 12:00\n"))
        typed = [l for l in self.calls.read_text().splitlines() if "send-keys" in l]
        self.assertEqual(len(typed), 1, typed)
        self.assertRegex(typed[0], r"-t %7 C-u ; send-keys -t %7 -l closed s1 ; run-shell sleep 0.5 ; send-keys -t %7 Enter$")

    def test_spawn_hands_over_to_a_newer_installed_release_once(self):
        fake = self.tmp / "fake"
        (fake / "bin").mkdir(parents=True)
        (fake / ".claude-plugin").mkdir()
        (fake / "bin" / "flowkit-spawn").write_text('#!/bin/sh\necho "fake $* reexec=${FLOWKIT_SPAWN_REEXEC:-unset}"\n')
        (fake / "bin" / "flowkit-spawn").chmod(0o755)
        (self.tmp / "claude" / "plugins").mkdir()
        (self.tmp / "claude" / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"plugins": {"handoff@flowkit": [{"installPath": str(fake)}]}}))
        def spawn(version):
            (fake / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": version}))
            out = subprocess.run([str(SPAWN_BIN), "--dry-run", "--name", "s2", "p.md"], capture_output=True,
                                 text=True, env=self.env)
            return out.stdout + out.stderr
        self.assertEqual(spawn("9.9.9"), "fake --dry-run --name s2 p.md reexec=1\n")
        self.assertNotIn("fake", spawn("0.0.1"), "an older install leaves the called copy in charge")

    def spawn_repo(self):
        """A git repo with one commit, and a conf that picks the background driver, so a --dry-run
        needs no tmux and gets past the refusals to what it would run."""
        repo = self.tmp / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "init"], check=True)
        self.env["XDG_CONFIG_HOME"] = str(self.tmp)
        (self.tmp / "flowkit").mkdir()
        (self.tmp / "flowkit" / "handoff.conf").write_text("driver=background\n")
        return repo

    def spawn(self, *args):
        return subprocess.run([str(SPAWN_BIN), "--dry-run", *args], capture_output=True, text=True, env=self.env)

    def test_spawn_overlooks_an_untracked_claude_dir(self):
        repo = self.spawn_repo()
        (repo / ".claude" / "worktrees" / "x").mkdir(parents=True)
        (repo / ".claude" / "worktrees" / "x" / "f").write_text("")
        prompt = self.tmp / "p.md"
        prompt.write_text("hi\n")
        out = self.spawn("--repo", str(repo), "--name", "s2", str(prompt))
        self.assertEqual((out.returncode, out.stderr), (0, ""))
        self.assertIn("driver: background\n", out.stdout)
        (repo / "work.txt").write_text("")
        out = self.spawn("--repo", str(repo), "--name", "s2", str(prompt))
        self.assertEqual(out.returncode, 6)
        self.assertIn("?? work.txt", out.stderr)
        self.assertNotIn(".claude", out.stderr)

    def test_spawn_copies_only_a_prompt_from_outside_the_store(self):
        repo = self.spawn_repo()
        store = self.tmp / "claude" / "spawned"
        store.mkdir()
        saved = store / "s2.md"
        saved.write_text("hi\n")
        out = self.spawn("--repo", str(repo), "--name", "s2", str(saved))
        self.assertEqual((out.returncode, out.stderr), (0, ""))
        self.assertIn(f"prompt: {saved}\n", out.stdout)
        self.assertIn(f'"$(cat {saved})"', out.stdout)
        self.assertEqual([f.name for f in store.iterdir()], ["s2.md"], "the saved prompt is the record, kept")
        outside = self.tmp / "p.md"
        outside.write_text("hi\n")
        out = self.spawn("--repo", str(repo), "--name", "s2", str(outside))
        copy = re.search(r"^prompt: (.*)$", out.stdout, re.M).group(1)
        self.assertRegex(copy, rf"^{re.escape(str(store))}/s2-\d{{8}}-\d{{6}}\.md$")
        self.assertIn(f'"$(cat {copy})"', out.stdout)

    def test_spawn_points_a_command_prompt_at_its_brief(self):
        repo = self.spawn_repo()
        store = self.tmp / "claude" / "spawned"
        store.mkdir()
        (store / "s2.md").write_text("/implement-story 1526354\n")
        out = self.spawn("--repo", str(repo), "--name", "s2", str(store / "s2.md"))
        self.assertEqual((out.returncode, out.stderr), (0, ""))
        self.assertRegex(out.stdout, r'--append-system-prompt "\$\(cat \S+/s2-\d{8}-\d{6}-brief\.md\)" "\$\(cat '
                         + re.escape(str(store / "s2.md")))
        self.assertNotIn("shared:", out.stdout)
        (store / "s3.md").write_text("Repo: /r   Session: s3\n\nToday: /implement-story is next\n")
        out = self.spawn("--repo", str(repo), "--name", "s3", str(store / "s3.md"))
        self.assertNotIn("--append-system-prompt", out.stdout, "a brief prompt is launched as before")

    def test_spawn_passes_no_chrome_only_while_the_chrome_question_is_unanswered(self):
        repo = self.spawn_repo()
        prompt = self.tmp / "p.md"
        prompt.write_text("hi\n")
        conf, answer, asked = self.tmp / "flowkit" / "handoff.conf", self.tmp / "claude" / ".claude.json", self.tmp / "asked"

        def claude(version="2.1.328", flag="--no-chrome"):
            (self.tmp / "bin" / "claude").write_text(
                "#!/bin/sh\n"
                'case "$1" in\n'
                "  agents) echo '[]' ;;\n"
                f"  --version) echo version >> {asked}; echo '{version} (Claude Code)' ;;\n"
                f"  --help) echo help >> {asked}; echo '  {flag}   Turn off Claude in Chrome' ;;\n"
                "esac\n")

        def passed():
            out = self.spawn("--repo", str(repo), "--name", "s2", str(prompt))
            self.assertEqual((out.returncode, out.stderr), (0, ""))
            return "claude --bg --no-chrome --remote-control " in out.stdout

        claude()
        self.assertFalse(passed(), "answered no")
        answer.write_text('{"claudeInChromeDefaultEnabled": true}')
        self.assertFalse(passed(), "answered yes")
        answer.write_text("{")
        self.assertFalse(passed(), "an answer that cannot be read")
        self.assertFalse(asked.exists(), "claude is asked nothing")
        answer.write_text('{"numStartups": 3}')
        self.assertTrue(passed(), "never answered, and --help lists the flag")
        self.assertTrue(passed())
        self.assertEqual(asked.read_text().count("help"), 1, "--help is asked once for a version")
        claude(version="2.1.100", flag="--chrome")
        self.assertFalse(passed(), "a claude that does not know the flag")
        self.assertEqual(asked.read_text().count("help"), 2, "another version is asked again")
        claude()
        answer.unlink()
        self.assertTrue(passed(), "no .claude.json yet: never answered")
        asked.unlink()
        conf.write_text(conf.read_text() + "chrome=on\n")
        self.assertFalse(passed(), "chrome=on leaves the flag out")
        self.assertFalse(asked.exists())

    def held_spawn(self, flag_lines, **env):
        """A background-driver launch (no --dry-run) with ~/.claude/load-hold holding flag_lines,
        or no flag for None, polling every 0.1 s; `claude --bg` answers with an id that `claude
        agents` lists. Returns the running process, the flag and the story file."""
        repo = self.spawn_repo()
        stub = self.tmp / "bin" / "claude"
        stub.write_text(f"#!/bin/sh\nif [ \"$1\" = agents ]; then echo '[{{\"id\":\"abc123\",\"sessionId\":\"abc12300-0000-0000-0000-000000000000\"}}]'; "
                        f"else echo \"$@\" >> {self.calls}; echo 'backgrounded · abc123'; fi\n")
        flag = self.tmp / "claude" / "load-hold"
        if flag_lines is not None:
            flag.write_text("".join(line + "\n" for line in flag_lines))
        prompt = self.tmp / "p.md"
        prompt.write_text("hi\n")
        proc = subprocess.Popen([str(SPAWN_BIN), "--no-rc", "--repo", str(repo), "--name", "s2", "--story", "st",
                                 str(prompt)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                env={**self.env, "FLOWKIT_HOLD_POLL": "0.1", **env})
        return proc, flag, self.tmp / "claude" / "stories" / "st.md"

    def test_spawn_waits_while_the_load_hold_is_set(self):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        proc, flag, story = self.held_spawn([now, "free memory 9%"])
        time.sleep(1)
        self.assertIsNone(proc.poll(), "held")
        self.assertFalse(self.calls.exists(), "nothing launched while held")
        self.assertRegex(story.read_text(), r"^# st\n> held s2 · \d{4}-\d\d-\d\d \d\d:\d\d · free memory 9%\n$")
        flag.unlink()
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, err)
        self.assertRegex(out, r"SPAWNED driver=background id=abc123 story=st name=s2 ")
        self.assertRegex(err, r"^launch held: free memory 9%; waiting until \S+/load-hold is gone, checking every "
                         r"0\.1 s\nlaunch hold lifted after 0 min; launching\n$")
        self.assertEqual(story.read_text().count("> held"), 1, "said once")
        self.assertIn("--bg", self.calls.read_text())

    def test_a_close_and_a_launchs_end_start_a_missing_watch_poller(self):
        watches = self.tmp / "claude" / "flowkit" / "watches"
        watches.mkdir(parents=True)
        self.env["FLOWKIT_WATCH_POLL"] = "0.2"
        close = "## s1 · 2026-10-27 12:00\nSession: x · Repo: /r\nNext: none\n"

        def poller():
            try:
                return int((watches / "poller.pid").read_text())
            except (OSError, ValueError):
                return None

        def comes_and_goes(after):
            deadline = time.time() + 10
            while poller() is None:
                self.assertLess(time.time(), deadline, f"no poller {after}")
                time.sleep(0.05)
            (watches / "probe.json").unlink()  # with its last watch the poller goes
            while poller() is not None:
                self.assertLess(time.time(), deadline, "the poller stayed with no watch open")
                time.sleep(0.05)

        self.addCleanup(lambda: poller() and os.kill(poller(), 15))
        self.handoff("record", "my-story", stdin=close)
        time.sleep(0.5)
        self.assertEqual(list(watches.iterdir()), [], "no watch is open: nothing is started")
        (watches / "probe.json").write_text(json.dumps({"name": "probe", "state": "fixing"}))
        self.handoff("record", "my-story", stdin=close)
        comes_and_goes("after a close")
        (watches / "probe.json").write_text(json.dumps({"name": "probe", "state": "fixing"}))
        proc, _, _ = self.held_spawn(None)
        out, err = proc.communicate(timeout=10)
        self.assertIn("SPAWNED driver=background", out, err)
        comes_and_goes("after a launch's end")

    def test_spawn_ignores_a_stale_load_hold(self):
        proc, _, story = self.held_spawn(["2026-01-01T00:00:00+00:00", "swap 9 GB"])
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, err)
        self.assertRegex(err, r"^launch hold ignored, stale: set \d+ min ago \(swap 9 GB\)\n$")
        self.assertRegex(story.read_text(), r"> hold ignored s2 · .* · stale, set \d+ min ago · swap 9 GB\n")
        self.assertIn("SPAWNED driver=background", out)

    def test_spawn_skips_the_load_hold_on_the_override(self):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        proc, _, story = self.held_spawn([now, "free memory 9%"], FLOWKIT_IGNORE_HOLD="1")
        out, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(err, "launch hold ignored, FLOWKIT_IGNORE_HOLD is set: free memory 9%\n")
        self.assertRegex(story.read_text(), r"^# st\n> launched s2 · [\d :-]+\n$", "nothing to say but the launch")
        self.assertIn("SPAWNED driver=background", out)

    def test_spawn_without_a_load_hold_says_nothing(self):
        proc, _, story = self.held_spawn(None)
        out, err = proc.communicate(timeout=10)
        self.assertEqual((proc.returncode, err), (0, ""))
        self.assertRegex(story.read_text(), r"^# st\n> launched s2 · \d{4}-\d\d-\d\d \d\d:\d\d\n$")
        self.assertIn("SPAWNED driver=background", out)

    def test_spawn_dry_run_reports_the_load_hold_without_waiting(self):
        repo = self.spawn_repo()
        (self.tmp / "claude" / "load-hold").write_text(datetime.now().isoformat() + "\nfree memory 9%\n")
        prompt = self.tmp / "p.md"
        prompt.write_text("hi\n")
        out = self.spawn("--repo", str(repo), "--name", "s2", "--story", "st", str(prompt))
        self.assertEqual(out.returncode, 0)
        self.assertRegex(out.stderr, r"^launch held: free memory 9%; a launch would wait until \S+ is gone\n$")
        self.assertFalse((self.tmp / "claude" / "stories" / "st.md").exists())

    def spawn_in_tmux(self, exits, dialog=""):
        """A throwaway tmux server whose one pane runs bash with a fake `claude` first on its PATH:
        the first `exits` launches exit 1 at once, the next writes its pid and sleeps; `claude
        agents --json` lists that pid once the file is there. With `dialog` the launch prints it
        and sleeps, never listed. Returns the pane id and the run."""
        repo = self.spawn_repo()
        (self.tmp / "flowkit" / "handoff.conf").write_text("driver=tmux\nreporter=off\n")
        (self.tmp / "bin" / "tmux").unlink()
        pidfile, launches, screen = self.tmp / "claude.pid", self.tmp / "launches", self.tmp / "screen"
        screen.write_text(dialog)
        fake = self.tmp / "bin" / "claude"
        fake.write_text(
            "#!/bin/sh\n"
            f"if [ \"$1\" = agents ]; then\n"
            f"  if [ -f {pidfile} ]; then printf '[{{\"name\":\"s2\",\"pid\":%s,\"kind\":\"interactive\","
            f"\"status\":\"idle\",\"cwd\":\"{repo}\"}}]' \"$(cat {pidfile})\"; else echo '[]'; fi\n"
            "  exit 0\n"
            "fi\n"
            f"echo launch >> {launches}\n"
            f"if [ \"$(wc -l < {launches})\" -le {exits} ]; then echo 'claude: boom' >&2; exit 1; fi\n"
            + (f"cat {screen}\n" if dialog else f"echo $$ > {pidfile}\n") +
            "exec sleep 30\n")
        fake.chmod(0o755)
        sock = f"flowkit-test-{os.getpid()}"
        self.tmux = real = lambda *a: subprocess.run(["tmux", "-L", sock, *a], capture_output=True, text=True)
        self.addCleanup(real, "kill-server")
        real("-f", "/dev/null", "new-session", "-d", "-x", "200", "-y", "50", "/bin/bash --noprofile --norc")
        pane = real("list-panes", "-F", "#{pane_id}").stdout.split()[0]
        real("set-option", "-p", "-t", pane, "@flowkit_parked", "1")  # the tick's stamp of the pane's last session
        # Typed, not `new-session -e`: tmux starts the command through the user's shell, whose rc
        # files rewrite PATH. The pane must never reach the real claude.
        which = self.tmp / "which"
        real("send-keys", "-t", pane, f"export PATH={self.tmp / 'bin'}:$PATH; command -v claude > {which}.tmp && mv {which}.tmp {which}; clear", "Enter")
        deadline = time.time() + 5
        while time.time() < deadline and not which.exists():
            time.sleep(0.05)
        self.assertEqual(which.read_text().strip(), str(fake), "the pane sees the fake claude")
        prompt = self.tmp / "p.md"
        prompt.write_text("hi\n")
        out = subprocess.run([str(SPAWN_BIN), "--tmux-socket", sock, "--pane", pane, "--no-rc", "--repo", str(repo),
                              "--name", "s2", str(prompt)], capture_output=True, text=True, env=self.env)
        return pane, out, launches

    @unittest.skipUnless(shutil.which("tmux"), "needs tmux")
    def test_spawn_types_the_launch_again_when_claude_exits_at_start_up(self):
        pane, out, launches = self.spawn_in_tmux(exits=1)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertRegex(out.stdout, rf"^SPAWNED pane=\d+ id={pane} tab=\S+ story=s2 name=s2 rc=off prompt=\S+ created=0 try=2\n$")
        self.assertEqual(out.stderr, f"warning: session 's2' exited at start-up in {pane}; typed again\n")
        self.assertEqual(launches.read_text(), "launch\nlaunch\n")

    @unittest.skipUnless(shutil.which("tmux"), "needs tmux")
    def test_spawn_gives_up_when_claude_exits_twice(self):
        pane, out, launches = self.spawn_in_tmux(exits=2)
        self.assertEqual(out.returncode, 10)
        self.assertEqual(out.stdout, "", "no SPAWNED line")
        self.assertEqual(out.stderr, f"warning: session 's2' exited at start-up in {pane}; typed again\n"
                         f"session 's2' exited twice at start-up in pane {pane}; its output is in the pane\n")
        self.assertEqual(launches.read_text(), "launch\nlaunch\n")

    @unittest.skipUnless(shutil.which("tmux"), "needs tmux")
    def test_spawn_reports_a_session_parked_at_a_start_up_prompt(self):
        hints = " Enter to confirm · Esc to cancel"
        pane, out, launches = self.spawn_in_tmux(exits=0, dialog="─" * 60 + "\n Accessing workspace:\n\n /Users/me/repo\n\n"
                                                 " ❯ No, exit\n   Yes, I trust this folder\n\n" + hints + "\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertRegex(out.stdout, rf"^SPAWNED pane=\d+ id={pane} tab=\S+ story=s2 name=s2 rc=off prompt=\S+ created=0 try=1 "
                         r"parked=Accessing workspace:\n$")
        self.assertEqual(out.stderr, f"warning: session 's2' is parked at a start-up prompt in {pane} "
                         "(Accessing workspace:); answer it in the pane\n")
        self.assertRegex((self.tmp / "claude" / "stories" / "s2.md").read_text(),
                         r"\n> parked s2 · \d{4}-\d\d-\d\d \d\d:\d\d · Accessing workspace:\n$")
        self.assertEqual(launches.read_text(), "launch\n", "typed once")
        self.assertEqual(self.tmux("capture-pane", "-p", "-t", pane).stdout.rstrip().splitlines()[-1], hints,
                         "nothing was typed at the prompt")
        self.assertEqual(self.tmux("show-options", "-pqv", "-t", pane, "@flowkit_parked").stdout, "",
                         "the launch clears the stamp of the pane's last session")

    def capacity_state(self):
        """What the capacity state holds: resource -> holder, the launching pids, the log's events."""
        state = self.tmp / "claude" / "capacity"
        log = state / "log.jsonl"
        return ({p.stem: json.loads(p.read_text())["session"] for p in (state / "resources").glob("*.json")},
                sorted(p.stem for p in (state / "launching").glob("*.json")),
                [json.loads(line)["event"] for line in log.read_text().splitlines()] if log.exists() else [])

    def test_spawn_gives_back_what_it_took_when_the_launch_fails(self):
        repo = self.spawn_repo()
        (self.tmp / "flowkit" / "capacity.conf").write_text(f"[{repo}]\nresource simulator = a device check\n")
        stub = self.tmp / "bin" / "claude"
        stub.write_text("#!/bin/sh\nif [ \"$1\" = agents ]; then echo '[]'; else echo boom; exit 1; fi\n")
        prompt = self.tmp / "p.md"
        prompt.write_text("Repo: /r   Session: s2\nNeeds: simulator\n\nToday: x\n")
        out = subprocess.run([str(SPAWN_BIN), "--no-rc", "--repo", str(repo), "--name", "s2", "--story", "st", str(prompt)],
                             capture_output=True, text=True, env=self.env)
        self.assertEqual(out.returncode, 9, out.stderr)
        self.assertEqual(self.capacity_state(), ({}, [], ["take", "launch", "release"]), "taken, then given back")
        self.assertRegex((self.tmp / "claude" / "stories" / "st.md").read_text(), r"> launched s2 · ")

    def test_spawn_from_a_watch_is_marked_and_reads_the_tree_after_the_gate(self):
        """A watch's fix slice for a repository of one checkout: queued behind the slice whose
        uncommitted work is in the tree, refused for a dirty tree only once the gate let it through."""
        repo = self.spawn_repo()
        (self.tmp / "flowkit" / "capacity.conf").write_text(f"[{repo}]\ncheckouts = 1\n")
        stub = self.tmp / "bin" / "claude"
        stub.write_text(f"#!/bin/sh\nif [ \"$1\" = agents ]; then echo '[{{\"id\":\"abc123\",\"sessionId\":\"abc12300-0000-0000-0000-000000000000\"}}]'; "
                        f"else echo \"$@\" >> {self.calls}; echo 'backgrounded · abc123'; fi\n")
        prompt = self.tmp / "p.md"
        prompt.write_text("Repo: /r   Session: st-fix-1\nNeeds: none\nLeft: 0\n\nToday: the fix.\n")
        state = self.tmp / "claude" / "capacity"
        lease = state / "resources" / "repo-checkout.json"
        lease.parent.mkdir(parents=True)
        lease.write_text(json.dumps({"session": f"launch:{os.getpid()}", "name": "st-s2", "taken": time.time()}))
        (repo / "work.txt").write_text("the running slice's\n")

        def spawn(*more):
            return subprocess.run([str(SPAWN_BIN), "--no-rc", "--repo", str(repo), "--name", "st-fix-1", "--story", "st", *more,
                                   str(prompt)], capture_output=True, text=True, env=self.env)

        self.assertEqual(spawn().returncode, 6, "any other launch is refused for the dirty tree first")
        out = spawn("--from-watch", "repo!12")
        self.assertEqual(out.returncode, 11, out.stderr)
        self.assertRegex(out.stdout, r"^QUEUED #1, ~\d\d:\d\d · id=1 · busy: repo-checkout is held by st-s2 since \d\d:\d\d\n$")
        entry = json.loads((state / "queue" / "1.json").read_text())
        self.assertEqual((entry["watch"], entry["by"], entry["argv"][-3:-1]), ("repo!12", "watch", ["--from-watch", "repo!12"]))
        (state / "queue" / "1.json").unlink()
        lease.unlink()
        out = spawn("--from-watch", "repo!12")
        self.assertEqual(out.returncode, 6, out.stderr)
        self.assertEqual(self.capacity_state()[:2], ({}, []), "what the gate took is given back")
        (repo / "work.txt").unlink()
        out = spawn("--from-watch", "repo!12")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("SPAWNED driver=background id=abc123 story=st name=st-fix-1 ", out.stdout)
        self.assertRegex((self.tmp / "claude" / "stories" / "st.md").read_text(),
                         r"\n> launched st-fix-1 · \d{4}-\d\d-\d\d \d\d:\d\d · launched-by: watch repo!12\n")
        log = [json.loads(line) for line in (state / "log.jsonl").read_text().splitlines()]
        self.assertEqual([(e["by"], e["watch"]) for e in log if e["event"] == "launch"][-1], ("watch", "repo!12"))

    @unittest.skipUnless(shutil.which("tmux"), "needs tmux")
    def test_a_full_machine_queues_and_a_close_drains_it(self):
        """slots = 1, held by a live chain `other`; a new chain is queued, and the close of other-s1
        launches it into a throwaway tmux server, its simulator handed to the new session."""
        repo = self.spawn_repo()
        (self.tmp / "flowkit" / "handoff.conf").write_text("driver=tmux\nreporter=off\n")
        (self.tmp / "flowkit" / "capacity.conf").write_text(f"slots = 1\n[{repo}]\nresource simulator = s\n")
        (self.tmp / "bin" / "tmux").unlink()
        other = self.tmp / "other"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        sleeper = subprocess.Popen(["sleep", "60"])
        self.addCleanup(sleeper.wait)
        self.addCleanup(sleeper.kill)
        sid = "0" * 8 + "-1111-1111-1111-" + "1" * 12
        fake = self.tmp / "fake"
        fake.mkdir()
        (fake / "rows.json").write_text(json.dumps([{"sessionId": sid, "name": "other-s1", "pid": sleeper.pid,
                                                      "cwd": str(other), "status": "busy", "kind": "interactive"}]))
        stories = self.tmp / "claude" / "stories"
        stories.mkdir(parents=True)
        (stories / "other.md").write_text("# other\n> launched other-s1 · 2026-11-13 14:00\n")
        claude = self.tmp / "bin" / "claude"
        claude.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys, time, uuid\n"
            f"fake = {str(fake)!r}\n"
            "if sys.argv[1:2] == ['agents']:\n"
            "    rows = json.load(open(fake + '/rows.json'))\n"
            "    for f in os.listdir(fake):\n"
            "        if f.startswith('live-'):\n"
            "            rows.append(json.load(open(os.path.join(fake, f))))\n"
            "    print(json.dumps(rows))\n"
            "    sys.exit(0)\n"
            "name = sys.argv[sys.argv.index('--name') + 1]\n"
            "row = {'sessionId': str(uuid.uuid4()), 'name': name, 'pid': os.getpid(), 'cwd': os.getcwd(),\n"
            "       'status': 'busy', 'kind': 'interactive', 'startedAt': int(time.time() * 1000)}\n"
            "open(os.path.join(fake, f'live-{os.getpid()}.json'), 'w').write(json.dumps(row))\n"
            "time.sleep(30)\n")
        claude.chmod(0o755)
        env = {k: v for k, v in self.env.items() if k not in ("TMUX", "TMUX_PANE", "CLAUDE_PID")}
        env["TMUX_TMPDIR"] = str(self.tmp)  # a server of its own: nothing reaches the user's tmux
        sock = f"ft-{os.getpid()}"
        real = lambda *a: subprocess.run(["tmux", "-L", sock, *a], capture_output=True, text=True, env=env)
        self.addCleanup(real, "kill-server")
        # every pane a bare bash with the fake claude first on its PATH: never the user's shell rc
        shell = f"env PATH={self.tmp / 'bin'}:/usr/bin:/bin /bin/bash --noprofile --norc"
        real("-f", "/dev/null", "new-session", "-d", "-s", "main", "-x", "200", "-y", "50", shell)
        real("set-option", "-g", "default-shell", "/bin/sh")
        real("set-option", "-g", "default-command", shell)
        prompt = self.tmp / "p.md"
        prompt.write_text("Repo: /r   Session: demo-s1\nNeeds: simulator\n\nToday: x\n")
        out = subprocess.run([str(SPAWN_BIN), "--tmux-socket", sock, "--tmux-session", "main", "--no-rc", "--repo", str(repo),
                              "--name", "demo-s1", "--story", "demo", str(prompt)], capture_output=True, text=True, env=env)
        self.assertEqual(out.returncode, 11, out.stderr)
        self.assertRegex(out.stdout, r"^QUEUED #1, ~\d\d:\d\d · id=1 · slots 1/1\n$")
        self.assertEqual(self.capacity_state()[:2], ({}, []), "a queued launch holds nothing")
        entry = json.loads((self.tmp / "claude" / "capacity" / "queue" / "1.json").read_text())
        self.assertIn("--tmux-socket", entry["argv"])
        self.assertEqual(entry["argv"][entry["argv"].index("--tmux-session") + 1], "main")
        self.assertEqual(list((fake).glob("live-*")), [], "nothing launched")
        close = f"## other-s1 · 2026-11-13 15:00\nSession: {sid}\n\nDONE\n- landed\n"
        rec = subprocess.run(["python3", str(HANDOFF), "record", "other"], input=close, capture_output=True, text=True, env=env)
        self.assertEqual(rec.returncode, 0, rec.stderr)
        self.assertRegex(rec.stdout, r"capacity: launched #1 demo-s1 \(flowkit-spawn pid \d+\)\n")
        log = self.tmp / "claude" / "capacity" / "launched" / "1.log"
        deadline = time.time() + 30
        while time.time() < deadline and "SPAWNED" not in (log.read_text() if log.exists() else ""):
            time.sleep(0.2)
        self.assertRegex(log.read_text(), r"SPAWNED pane=\d+ id=%\d+ tab=demo story=demo name=demo-s1 rc=off ")
        (live,) = fake.glob("live-*")
        new = json.loads(live.read_text())
        self.addCleanup(lambda: subprocess.run(["kill", str(new["pid"])], capture_output=True))
        while time.time() < deadline and self.capacity_state()[1]:
            time.sleep(0.2)
        held, launching, events = self.capacity_state()
        self.assertEqual((held, launching), ({"simulator": new["sessionId"]}, []), "handed to the new session")
        self.assertEqual(events, ["queue", "take", "dequeue", "repoint"])
        story = (stories / "demo.md").read_text()
        self.assertRegex(story, r"> queued demo-s1 · [\d :-]+ · #1, ~\d\d:\d\d · id=1 · slots 1/1\n> launched demo-s1 · ")

    def test_refresh_prunes_the_old_prompt_copies(self):
        self.env["XDG_CONFIG_HOME"] = str(self.tmp)  # no installed kit in the sandbox: refresh copies nothing
        store = self.tmp / "claude" / "spawned"
        store.mkdir()
        old = time.time() - 31 * 86400
        for name in ("s2-20260926-120000.md", "s2-20260926-120000-shared.md", "my-story-report-20260926-120000.md",
                     "s2.md", "s3-20261027-120000.md"):
            (store / name).write_text("x\n")
            os.utime(store / name, (old, old) if name != "s3-20261027-120000.md" else None)
        out = self.handoff("refresh", str(PLUGIN))
        self.assertEqual((out.returncode, out.stdout), (0, "spawned: 3 prompt copies older than 30 days removed\n"))
        self.assertEqual(sorted(f.name for f in store.iterdir()), ["s2.md", "s3-20261027-120000.md"])
        self.assertEqual(self.handoff("refresh", str(PLUGIN)).stdout, "")

    def installed_kit(self):
        """A sandbox HOME and config dir with the kit installed (its tmux.conf in place)."""
        home, conf = self.tmp / "home", self.tmp / "xdg" / "flowkit"
        conf.mkdir(parents=True)
        (conf / "tmux.conf").write_text("installed\n")
        self.env.update(HOME=str(home), XDG_CONFIG_HOME=str(conf.parent))
        return home / ".local" / "bin" / "flowkit-tmux", conf / "kit-version"

    def plugin_root(self, version):
        root = self.tmp / f"root-{version}"
        for rel in ("tmux/tmux.conf", "tmux/cheatsheet.md", "tmux/capacity-shim.py", "bin/flowkit-tmux",
                    "bin/flowkit-away", "bin/flowkit-usage"):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(f"{rel} {version}\n")
        (root / ".claude-plugin").mkdir(exist_ok=True)
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": version}))
        return str(root)

    def test_refresh_copies_the_kit_forward_only(self):
        tick, stamp = self.installed_kit()
        def refresh(version):
            out = self.handoff("refresh", self.plugin_root(version))
            self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
            return tick.read_text(), stamp.read_text()
        self.assertEqual(refresh("0.8.1"), ("bin/flowkit-tmux 0.8.1\n", "0.8.1\n"), "no stamp: the first copy")
        self.assertEqual(refresh("0.8.0"), ("bin/flowkit-tmux 0.8.1\n", "0.8.1\n"), "an older root leaves it alone")
        tick.write_text("bin/flowkit-tmux 0.8.0\n")  # a pre-stamp release's refresh put its copy back
        self.assertEqual(refresh("0.8.1"), ("bin/flowkit-tmux 0.8.1\n", "0.8.1\n"), "the same version copies again")
        self.assertEqual(refresh("0.10.0"), ("bin/flowkit-tmux 0.10.0\n", "0.10.0\n"), "versions compare as numbers")

    def test_the_kit_hands_a_0_8_0_tick_drain_to_the_installed_capacity(self):
        """A 0.8.0 tick starts ~/.local/scripts/capacity.py: the kit puts a resolver there."""
        tick, _ = self.installed_kit()
        self.assertEqual(self.handoff("refresh", str(PLUGIN)).returncode, 0)
        newest = Path(self.plugin_root("9.9.9"))
        (newest / "scripts").mkdir()
        (newest / "scripts" / "capacity.py").write_text("import sys\nprint(__file__, *sys.argv[1:])\n")
        (self.tmp / "claude" / "plugins").mkdir()
        (self.tmp / "claude" / "plugins" / "installed_plugins.json").write_text(json.dumps({"plugins": {
            "handoff@flowkit": [{"installPath": str(self.tmp / "gone")}, {"installPath": str(PLUGIN)},
                                {"installPath": str(newest)}]}}))
        shim = tick.parent.parent / "scripts" / "capacity.py"
        out = subprocess.run([sys.executable, str(shim), "drain"], capture_output=True, text=True, env=self.env)
        self.assertEqual((out.returncode, out.stdout), (0, f"{newest / 'scripts' / 'capacity.py'} drain\n"), out.stderr)

    def test_record_says_what_the_doorbell_did(self):
        def recorded(name):
            out = self.handoff("record", "my-story", stdin=f"## {name} · 2026-10-27 12:00\nSession: x · Repo: /r\nNext: none\n")
            self.assertEqual(out.returncode, 0, out.stderr)
            return out.stdout.splitlines()[1:]
        self.assertEqual(recorded("s1"), ["reporter: none"])
        self.agents([{"kind": "interactive", "name": "my-story-report", "pid": os.getpid(), "status": "waiting"}])
        self.panes(f"{os.getppid()} %7")
        self.assertEqual(recorded("s2"), ["reporter: waiting"])
        self.agents([{"kind": "interactive", "name": "my-story-report", "pid": os.getpid(), "status": "idle"}])
        self.assertEqual(recorded("s3"), ["reporter: rung"])
        self.agents([{"kind": "background", "name": "my-story-report", "id": "b1", "sessionId": "B1", "status": "idle"}])
        self.assertEqual(recorded("s4"), ["reporter: background"])
        out = self.handoff("record", "my-story", stdin="## Final report · 2026-10-27 12:05\nall done\n")
        self.assertEqual(out.stdout.splitlines()[1:], [], "a final report rings nobody")

    def test_nudge_sorts_the_waiting_sessions(self):
        me, shell = os.getpid(), os.getppid()
        self.agents([
            {"name": "s1", "kind": "interactive", "pid": me, "status": "waiting", "waitingFor": "input needed"},
            {"name": "s2", "kind": "interactive", "pid": 1, "status": "waiting", "waitingFor": "input needed"},
            {"name": "s3", "kind": "interactive", "pid": 1, "status": "waiting", "waitingFor": "permission prompt"},
            {"name": "x-report", "kind": "interactive", "pid": 1, "status": "waiting", "waitingFor": "input needed"},
            {"name": "s4", "kind": "background", "id": "b4", "sessionId": "B4", "pid": 1, "status": "waiting",
             "waitingFor": "input needed"},
            {"name": "s5", "kind": "interactive", "pid": 1, "status": "waiting", "waitingFor": "dialog open"},
            {"name": "s6", "kind": "interactive", "pid": 1, "status": "idle"},
        ])
        self.panes(f"{shell} %7")
        out = self.away("nudge", "--dry-run")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.splitlines(), [
            "would nudge s1 (tmux %7)",
            "unreachable s2: waits on a question outside tmux; answer it from the phone",
            "permission s3: waits on a permission prompt; answer it from the phone",
            "skipped x-report: a reporter never asks",
            "would nudge s4 (background)",
            "waiting s5: waits on dialog open",
        ])
        self.assertFalse(self.calls.exists())
        only = self.away("nudge", "--dry-run", "--only", "s6", "--only", "s3").stdout.splitlines()
        self.assertEqual(only, ["permission s3: waits on a permission prompt; answer it from the phone",
                                "s6: not waiting (idle)"])

    def test_nothing_waits(self):
        self.agents([{"name": "s1", "kind": "interactive", "pid": 1, "status": "busy"}])
        self.assertEqual(self.away("nudge").stdout.strip(), "no session waits on you")
        self.assertEqual(self.away("waiting").stdout.strip(), "nobody waits on you")
        self.assertEqual(self.away("nudge", "--what").returncode, 2)

    def test_away_writes_the_flag_the_nudge_runs_once_in_effect_and_back_lists(self):
        self.agents([
            {"name": "s3", "kind": "interactive", "pid": 1, "status": "waiting", "waitingFor": "permission prompt"},
        ])
        self.usage_answer()
        out = self.handoff("away").stdout.splitlines()
        self.assertTrue(self.flag.is_file())
        self.assertTrue(out[0].startswith("away: from "))
        self.assertTrue(out[1].startswith("Week 49% used, resets "))
        self.assertEqual(len(out), 2, "no nudge before the flag takes effect")
        nudge = lambda: self.handoff("away-nudge").stdout
        self.assertEqual(nudge(), "", "not in effect yet")
        self.set_flag(time.time() - 60, by="coord-a")
        self.assertEqual(nudge(), "permission s3: waits on a permission prompt; answer it from the phone\n")
        self.assertEqual(nudge(), "", "once per flag")
        self.set_flag(time.time() - 13 * 3600, by="coord-b", written=time.time() - 13 * 3600)
        self.assertEqual(nudge(), "", "an expired flag gets none")
        out = self.handoff("back").stdout.splitlines()
        self.assertFalse(self.flag.exists())
        self.assertEqual(out, ["away: off", "s3: waits on permission prompt"])
        self.assertFalse(self.calls.exists())
        self.assertEqual(nudge(), "")

    def asks(self, name, sid, status="waiting"):
        return {"name": name, "kind": "interactive", "pid": os.getpid(), "sessionId": sid, "status": status,
                "waitingFor": "input needed"}

    def nudging_tmux(self):
        """A tmux whose one pane, %7, is this test's shell, and in which Escape lets a session leave its
        question: from then on `claude agents` answers what next.json holds."""
        stub, then = self.tmp / "bin" / "tmux", self.tmp / "next.json"
        stub.write_text(f"#!/bin/sh\ncase \"$1\" in\nlist-panes) echo '{os.getppid()} %7';;\n"
                        f"send-keys) echo tmux \"$@\" >> {self.calls}; "
                        f"if [ \"$4\" = Escape ] && [ -f {then} ]; then mv {then} {self.tmp / 'agents.json'}; fi;;\n"
                        f"*) echo tmux \"$@\" >> {self.calls};; esac\n")
        stub.chmod(0o755)
        return then

    def escaped(self, wait):
        """Whether a nudge's Escape was typed within `wait` seconds."""
        deadline = time.time() + wait
        while time.time() < deadline and not (self.calls.exists() and "Escape" in self.calls.read_text()):
            time.sleep(0.05)
        return self.calls.exists() and "Escape" in self.calls.read_text()

    def test_the_nudge_leaves_a_session_the_user_is_answering_for_a_later_pass(self):
        self.agents([self.asks("demo-coord", "sa"), self.asks("demo-s2", "sb")])
        then = self.nudging_tmux()
        self.set_flag(time.time() - 60, by="coord-b")
        mark, nudged = self.tmp / "flowkit-context-sa.json", self.tmp / "claude" / "flowkit" / "away-nudged"
        mark.write_text(json.dumps({"prompted": True, "typed": time.time() - 60}))
        then.write_text(json.dumps([self.asks("demo-coord", "sa"), self.asks("demo-s2", "sb", "idle")]))
        nudge = lambda: self.handoff("away-nudge").stdout.splitlines()
        left = "present demo-coord: the user typed to it in the last 5 minutes; nudged once those are over"
        self.assertEqual(nudge(), [left, "nudged demo-s2 (tmux %7)"])
        self.assertEqual(self.calls.read_text().count("Escape"), 1, "the question the user is answering stays open")
        self.assertFalse(nudged.exists(), "one was left: a later pass comes back for it")
        self.assertEqual(nudge(), [left])
        self.assertEqual(self.calls.read_text().count("Escape"), 1)
        mark.write_text(json.dumps({"prompted": True, "typed": time.time() - 301}))
        then.write_text(json.dumps([self.asks("demo-coord", "sa", "idle"), self.asks("demo-s2", "sb", "idle")]))
        self.assertEqual(nudge(), ["nudged demo-coord (tmux %7)"], "five minutes after the last prompt typed to it")
        self.assertEqual(nudged.read_text(), self.flag.read_text())
        self.assertEqual(nudge(), [], "once per flag")
        self.set_flag(time.time() - 120, by="coord-b")
        (self.tmp / "agents.json").write_text("{")
        self.assertEqual(nudge(), ["cannot read `claude agents --json`: no session nudged"],
                         "who is present is unknown: the nudge of everybody, as it was")

    def test_away_starts_the_waiter_that_nudges_once_the_flag_is_in_effect(self):
        self.agents([self.asks("demo-s2", "sb")])
        then = self.nudging_tmux()
        self.usage_answer()
        self.env["FLOWKIT_RETRY_MINUTE"] = "0.2"  # the waiter's minute
        mark, nudged = self.tmp / "flowkit-context-sb.json", self.tmp / "claude" / "flowkit" / "away-nudged"
        self.handoff("away", "--by", "coord-a")
        self.assertFalse(self.escaped(0.6), "not before the flag takes effect")
        mark.write_text(json.dumps({"prompted": True, "typed": time.time()}))
        self.set_flag(time.time() - 60, by="coord-a")
        self.assertFalse(self.escaped(0.8), "in effect, and the user is answering its question")
        self.assertFalse(nudged.exists())
        then.write_text(json.dumps([self.asks("demo-s2", "sb", "idle")]))
        mark.write_text(json.dumps({"prompted": True, "typed": time.time() - 301}))
        self.assertTrue(self.escaped(5), "the waiter comes back for it, with no tick and no tmux client")
        deadline = time.time() + 5
        while time.time() < deadline and not nudged.exists():
            time.sleep(0.05)
        self.assertEqual(nudged.read_text(), self.flag.read_text())
        waiter = lambda: subprocess.Popen([sys.executable, str(HANDOFF), "away-nudge", "--wait"], env=self.env)
        self.assertEqual(waiter().wait(timeout=5), 0, "its flag was nudged: nothing to wait for")
        self.set_flag(time.time() + 240, by="coord-a")
        proc = waiter()
        self.addCleanup(lambda: (proc.kill(), proc.wait()))
        time.sleep(0.5)
        self.assertIsNone(proc.poll(), "it waits for the flag to take effect")
        self.flag.unlink()
        self.assertEqual(proc.wait(timeout=5), 0, "and ends with the flag")

    def test_away_takes_effect_five_minutes_on_and_counts_twelve_hours_from_then(self):
        self.usage_answer()
        out = self.handoff("away", "--by", "coord-a").stdout.splitlines()
        lines = self.flag.read_text().splitlines()
        at = datetime.strptime(lines[0], "%Y-%m-%d %H:%M").timestamp()
        self.assertTrue(time.time() + 240 < at <= time.time() + 360, "five minutes on, up to the minute")
        self.assertEqual(lines[1], "by: coord-a")
        self.assertRegex(lines[2], r"^set: \d{4}-\d\d-\d\d \d\d:\d\d$")
        self.assertLess(at - datetime.strptime(lines[2][5:], "%Y-%m-%d %H:%M").timestamp(), 7 * 60)
        self.assertEqual(out[0], f"away: from {lines[0]} (set by coord-a; questions go through until then; "
                                 "clears with back or after 12 hours)")
        self.assertIsNone(self.gate(), "not in effect before")
        self.assertEqual(self.handoff("away-status").stdout, f"away: from {lines[0]} (set by coord-a at {lines[2][5:]})\n")
        again = self.handoff("away", "--by", "coord-b").stdout.splitlines()[0]
        self.assertEqual(again, f"away: from {lines[0]} (already set by coord-a at {lines[2][5:]}; clears with back "
                                "or after 12 hours)")
        self.assertEqual(self.flag.read_text().splitlines(), lines, "a flag already set stays as it is")
        stamp = self.set_flag(time.time() - 60, by="coord-a")
        self.assertEqual(self.gate()["permissionDecision"], "deny", "in effect once its stamp has passed")
        self.assertEqual(self.handoff("away-status").stdout, f"away: since {stamp}\n")
        self.set_flag(time.time() - 12 * 3600 + 120, by="coord-a", written=time.time() - 12 * 3600 - 180)
        self.assertIsNotNone(self.gate(), "12 hours from the stamp it took effect, not from its write")
        self.set_flag(time.time() - 12 * 3600 - 120, by="coord-a", written=time.time() - 12 * 3600 - 420)
        self.assertIsNone(self.gate())
        self.assertEqual(self.handoff("away-status").stdout, "away: off\n")
        stamp = self.set_flag(time.time() - 60)
        self.assertEqual(self.gate()["permissionDecision"], "deny", "a raw one-line flag is in effect from its stamp")
        self.assertIn(f"since {stamp}", self.gate()["permissionDecisionReason"])
        self.set_flag(time.time() + 600)
        self.assertIsNone(self.gate(), "a raw stamp still ahead is not in effect yet")

    def test_away_rings_each_idle_coordinator_once(self):
        (self.tmp / "claude" / "coordination").mkdir()
        (self.tmp / "claude" / "coordination" / "demo.md").write_text("# demo\nCoordinator: `boss` · since 09:00\n")
        rows = [("boss", "idle"), ("demo-coord", "idle"), ("coord-b", "busy"), ("coord-demo-report", "idle"),
                ("demo-s2", "idle"), ("coord-c", "idle"), ("coord-d", "idle"), ("coord-w", "waiting")]
        self.agents([{"name": n, "kind": "interactive", "pid": 101 + i, "status": s} for i, (n, s) in enumerate(rows)]
                    + [{"name": "coord-e", "kind": "background", "pid": 120, "status": "idle"}])
        stub = self.tmp / "bin" / "tmux"
        stub.write_text("#!/bin/sh\ncase \"$1\" in\n"
                        "list-panes) printf '%s\\n' " + " ".join(f"'{101 + i} %{i + 1} 0'" for i in range(8)) + ";;\n"
                        "capture-pane) if [ \"$4\" = %7 ]; then echo '│ > half typed'; else echo '│ > '; fi;;\n"
                        f"*) echo tmux \"$@\" >> {self.calls};; esac\n")
        stub.chmod(0o755)
        self.usage_answer()
        out = self.handoff("away", "--by", "coord-c").stdout.splitlines()
        when = self.flag.read_text().splitlines()[0][-5:]
        self.assertEqual(out[2:], ["last call boss (tmux %1)", "last call demo-coord (tmux %2)",
                                   "no last call coord-b: busy", "no last call coord-d: draft",
                                   "no last call coord-w: waiting", "no last call coord-e: not in a tmux pane"])
        typed = self.calls.read_text().splitlines()
        self.assertEqual([t.split(" -l ")[0] for t in typed], ["tmux send-keys -t %1", "tmux send-keys -t %2"])
        self.assertTrue(typed[0].split(" -l ")[1].startswith(f"last call: the user goes away at {when}. Ask now "
                                                             "anything you need from them;"))
        self.assertTrue(typed[0].endswith("; run-shell sleep 0.5 ; send-keys -t %1 Enter"))
        self.handoff("away", "--by", "coord-b")
        self.assertEqual(len(self.calls.read_text().splitlines()), 2, "a flag already set rings nobody again")

    def test_a_session_title_with_spaces_sets_and_clears_the_flag(self):
        self.agents([{"name": "coord planning", "kind": "interactive", "pid": 101, "status": "idle"}])
        self.panes("101 %1 0")
        self.usage_answer()
        out = self.handoff("away", "--by", "coord planning")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.flag.read_text().splitlines()[1], "by: coord-planning")
        self.assertNotIn("last call", out.stdout, "the setter gets none, whatever its name holds")
        out = self.handoff("back", "--by", "…")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertFalse(self.flag.exists())
        self.assertEqual(json.loads((self.tmp / "claude" / "flowkit" / "away-cleared").read_text())["by"], "unknown")

    def test_away_without_tmux_says_so_per_coordinator(self):
        self.agents([{"name": n, "kind": "interactive", "pid": 101 + i, "status": "idle"}
                     for i, n in enumerate(("demo-coord", "coord-b"))])
        (self.tmp / "bin" / "tmux").unlink()
        os.symlink(shutil.which("cat"), self.tmp / "bin" / "cat")
        self.env["PATH"] = str(self.tmp / "bin")  # the stub claude and what it needs: no tmux on this machine
        self.usage_answer()
        out = subprocess.run([sys.executable, str(HANDOFF), "away", "--by", "x"], capture_output=True, text=True, env=self.env)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertTrue(self.flag.is_file())
        self.assertEqual([x for x in out.stdout.splitlines() if "last call" in x],
                         [f"no last call {n}: tmux or ps did not answer (FileNotFoundError)" for n in ("demo-coord", "coord-b")])
        self.assertEqual(out.stderr, "")

    def test_the_gate_lets_a_session_ask_within_five_minutes_of_a_prompt_the_user_typed(self):
        self.set_flag(time.time() - 60, by="coord-a")
        mark = self.tmp / "flowkit-context-s1.json"
        denied = lambda: (self.gate("s1") or {}).get("permissionDecision") == "deny"
        self.context(10, "Repo: /Users/me/repo   Session: demo-s1\n\nToday: …")
        self.assertTrue(denied(), "a session's first prompt is its launch brief")
        for prompt in ("<task-notification>\n<task-id>b1</task-id>", "Another Claude session sent a message: "
                       '<cross-session-message from="uds:/tmp/x">hi</cross-session-message>',
                       "[Cross-session idle notice] demo-s2 is idle", "<command-name>/model</command-name>",
                       "<local-command-stdout>Set model</local-command-stdout>", "<bash-input>ls</bash-input>",
                       "Your last turn stopped on the usage limit at 03:00. Carry on from where you were.",
                       "Your last turn ended on an API error (overloaded) at 03:00. Carry on from where you were.",
                       "Your last turn hit the output token limit at 03:00. Carry on, in smaller pieces.",
                       "away since 2026-11-19 23:15: I am off. Answer the question you asked with the recommended option",
                       "away: I am off. Answer the question", "report now", "closed demo-s2",
                       "last call: the user goes away at 23:15. Ask now anything you need from them",
                       "reminder: from flowkit's tick, not the user. Send the user one push notification now",
                       "wake: S2 merged: rebase onto main and carry on"):
            self.context(10, prompt)
            self.assertTrue(denied(), prompt)
        self.context(10, "go with 2, and keep the old name")
        self.assertFalse(denied(), "typed by the user a moment ago")
        self.assertEqual(self.gate("s2")["permissionDecision"], "deny", "another session is still denied")
        mark.write_text(json.dumps({**json.loads(mark.read_text()), "typed": time.time() - 301}))
        self.assertTrue(denied(), "five minutes on")
        self.handoff("context", stdin=json.dumps({"session_id": "s1", "hook_event_name": "PostToolUse",
                                                   "tool_name": "AskUserQuestion", "tool_response": {}}))
        self.assertFalse(denied(), "an answered question counts too")
        self.flag.unlink()
        self.assertIsNone(self.gate("s2"))

    def test_every_session_hears_the_set_and_the_clear_once(self):
        self.usage_answer()
        hear = lambda sid, prompt=None: self.context(10, prompt, sid=sid)
        self.assertIsNone(hear("s1", "hi"))
        self.handoff("away", "--by", "coord-a")
        stamp, _, now = self.flag.read_text().splitlines()
        line = (f"Away flag set by coord-a at {now[5:]}: the user is away from {stamp}. Until then questions go "
                "through; from then on a question is denied unless the user typed to this session in the 5 "
                'minutes before it, and you take the recommended option, marked "(auto)".')
        self.assertEqual(hear("s1"), line, "mid-run, at the next batch of tool calls")
        self.assertIsNone(hear("s1"))
        self.assertIsNone(hear("s1", "next"))
        self.assertEqual(hear("s2", "hi"), line)
        self.assertIsNone(hear("s2", "hi"))
        self.handoff("back", "--by", "coord-b")
        now = json.loads((self.tmp / "claude" / "flowkit" / "away-cleared").read_text())["at"]
        cleared = f"Away flag cleared by coord-b at {now}: the user is back, questions go to them again."
        self.assertEqual(hear("s1"), cleared)
        self.assertIsNone(hear("s1"))
        self.assertEqual(hear("s2", "hi"), cleared)
        self.assertIsNone(hear("s3", "hi"), "a session that never heard of it hears nothing")
        stamp = self.set_flag(time.time() - 60)
        self.assertEqual(hear("s1"), f"Away flag set by unknown at {stamp}, in effect since {stamp}: a question is "
                                     "denied unless the user typed to this session in the last 5 minutes; take the "
                                     'recommended option, marked "(auto)".')
        old = time.time() - 13 * 3600
        self.set_flag(old, written=old)
        self.assertEqual(hear("s1"), "Away flag expired (12 hours after it took effect): questions go to the user again.")
        self.set_flag(time.time() - 60)
        hear("s1")
        self.flag.unlink()
        self.assertEqual(hear("s1"), "Away flag removed: the user is back, questions go to them again.")
        ctx = self.context(45, "again", sid="s1")
        self.assertTrue(ctx.startswith("Context ~45%"), "the context line comes alone when the flag did not move")

    def test_restamp_keeps_an_unattended_chain_unattended(self):
        self.assertEqual(self.handoff("restamp").stdout, "away: off\n")
        self.assertFalse(self.flag.exists())
        self.flag.write_text("2026-10-27 01:10\n")
        old = time.time() - 11 * 3600
        os.utime(self.flag, (old, old))
        out = self.handoff("restamp").stdout
        self.assertRegex(out, r"^away: since 2026-10-27 01:10 \(re-stamped \d\d:\d\d; clears with back or after 12 hours\)\n$")
        self.assertLess(time.time() - self.flag.stat().st_mtime, 60)
        self.assertEqual(self.flag.read_text(), "2026-10-27 01:10\n")
        self.assertEqual(self.handoff("away-status").stdout, "away: since 2026-10-27 01:10\n")
        self.assertFalse(self.calls.exists(), "no nudge")
        self.assertFalse((self.tmp / "probes").exists(), "no usage call")
        stamp = self.set_flag(time.time() + 240, by="coord-a")
        self.assertRegex(self.handoff("restamp").stdout, rf"^away: from {stamp} \(set by coord-a at {stamp}\) "
                         r"\(re-stamped \d\d:\d\d; clears with back or after 12 hours\)\n$", "one line, and not `since` before it is")

    def usage_answer(self, hours=15):
        stamp = datetime.fromtimestamp(time.time() + hours * 3600 + 60, timezone.utc).isoformat()
        limits = [
            {"kind": "session", "percent": 9, "resets_at": stamp, "scope": None},
            {"kind": "weekly_all", "percent": 49, "resets_at": stamp, "scope": None},
            {"kind": "weekly_scoped", "percent": 37, "resets_at": stamp,
             "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}},
        ]
        answer = {"type": "control_response", "response": {
            "subtype": "success", "request_id": "flowkit-usage", "response": {"rate_limits": {
                "limits": limits,
                "weekly_scoped_shares": [
                    {"limit_index": 2, "used_percent_of_weekly": 19, "allowance_percent_of_weekly": 50}]}}}}
        (self.tmp / "usage.jsonl").write_text('{"type":"system","subtype":"hook_started"}\n' + json.dumps(answer) + "\n")
        return time.strftime("%a %H:%M", time.localtime(time.time() + hours * 3600 + 60))

    def probes(self):
        path = self.tmp / "probes"
        return len(path.read_text().splitlines()) if path.exists() else 0

    def test_usage_line_then_cache_per_config_folder(self):
        when = self.usage_answer()
        out = self.handoff("usage")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), f"Week 49% used, resets {when} (in 15 h) · Fable 37% of its bucket, "
                                             "19% of the week (cap 50%) · 5-hour 9%")
        (self.tmp / "usage.jsonl").unlink()
        cached = json.loads(self.handoff("usage", "--json").stdout)
        self.assertEqual((cached["source"], cached["models"][0]["week_share"], self.probes()), ("cache", 19, 1))
        self.env["CLAUDE_CONFIG_DIR"] = str(self.tmp / "other")
        self.assertEqual(json.loads(self.handoff("usage", "--json").stdout)["source"], "old")
        self.assertEqual(self.probes(), 2)

    def login(self, account, organization="org-1", path=None):
        """A fake account file: who is logged in under the test's CLAUDE_CONFIG_DIR, or at `path`."""
        (path or self.tmp / "claude" / ".claude.json").write_text(json.dumps({
            "claudeInChromeDefaultEnabled": False,
            "oauthAccount": {"accountUuid": account, "organizationUuid": organization, "displayName": "Me"}}))
        return {"account": account, "organization": organization}

    def two_logins(self):
        """Two logins on one config folder, as Claude Code keeps them: the home's ~/.claude is the
        test's config folder; with CLAUDE_CONFIG_DIR unset the account file is ~/.claude.json, with
        it set to ~/.claude it is the one inside. Returns (who is which, the folder's spelling)."""
        home = self.tmp / "home"
        home.mkdir()
        (home / ".claude").symlink_to(self.tmp / "claude")
        self.env["HOME"] = str(home)
        return self.login("acct-home", path=home / ".claude.json"), self.login("acct-folder"), str(home / ".claude")

    def test_usage_is_kept_and_named_per_account(self):
        plain, folder, spelled = self.two_logins()
        self.usage_answer()
        ask = lambda *args: json.loads(self.handoff("usage", "--json", *args).stdout)
        del self.env["CLAUDE_CONFIG_DIR"]
        first = ask()
        self.assertEqual((first["source"], first["account"], self.probes()), ("live", plain, 1))
        self.env["CLAUDE_CONFIG_DIR"] = spelled
        other = ask()
        self.assertEqual((other["source"], other["account"], self.probes()), ("live", folder, 2),
                         "the same folder, another login: never the first one's answer")
        del self.env["CLAUDE_CONFIG_DIR"]
        again = ask()
        self.assertEqual((again["source"], again["account"], self.probes()), ("cache", plain, 2))
        swapped = self.login("acct-home", "org-2", path=self.tmp / "home" / ".claude.json")
        after = ask()
        self.assertEqual((after["source"], after["account"], self.probes()), ("live", swapped, 3),
                         "a /login to another organization: the answer before it is not this account's")
        (self.tmp / "usage.jsonl").unlink()
        old = json.loads(subprocess.run(["python3", str(PLUGIN / "bin" / "flowkit-usage"), "--json", "--live"],
                                        capture_output=True, text=True, env=self.env).stdout)
        self.assertEqual((old["source"], old["account"]), ("old", None), "figures of no call name no account")

    def test_usage_falls_back_to_the_newest_week_file(self):
        out = self.handoff("usage")
        self.assertEqual(out.returncode, 1)
        self.assertEqual(out.stdout.strip(),
                         "usage: the live call failed (no answer (exit 1)), and no status line has recorded a week")
        weeks = self.tmp / "claude" / "usage-stats"
        weeks.mkdir()
        (weeks / "week-1.json").write_text('{"seven_day_pct": 10, "resets_at": 1}')
        (weeks / "week-2.json").write_text(json.dumps({"seven_day_pct": 49, "resets_at": int(time.time()) + 7200,
                                                       "five_hour_pct": 11, "updated": "2026-10-20T09:50:55Z"}))
        out = self.handoff("usage")
        self.assertEqual(out.returncode, 0)
        self.assertRegex(out.stdout.strip(), r"^usage: the live call failed \(no answer \(exit 1\)\); old figures "
                                             r"from the status line at \w+ \d\d:\d\d: Week 49% used, resets "
                                             r"\w+ \d\d:\d\d \(in 2 h\) · 5-hour 11%$")
        self.assertEqual(json.loads(self.handoff("usage", "--json").stdout)["models"], [])

    def context(self, pct, prompt=None, calls=(), sid="s1", name="story-s3", requests=30, **extra):
        stats = self.tmp / "claude" / "session-stats"
        stats.mkdir(parents=True, exist_ok=True)
        if pct != "missing":
            (stats / f"{sid}.json").write_text(json.dumps({"session_id": sid, "session_name": name, "ctx_pct": pct}))
        transcript = self.tmp / f"{sid}.jsonl"
        transcript.write_text("".join(f'{{"requestId":"req_{i}"}}\n' for i in range(requests)))
        data = {"session_id": sid, "transcript_path": str(transcript), **extra}
        if prompt is not None:
            data.update(hook_event_name="UserPromptSubmit", prompt=prompt)
        else:
            data.update(hook_event_name="PostToolBatch", tool_calls=list(calls))
        out = self.handoff("context", stdin=json.dumps(data)).stdout
        if not out:
            return None
        hook = json.loads(out)["hookSpecificOutput"]
        self.assertEqual(hook["hookEventName"], "UserPromptSubmit" if prompt is not None else "PostToolBatch")
        return hook["additionalContext"]

    def test_context_every_prompt_at_40(self):
        self.assertIsNone(self.context(39, "hi"))
        self.assertTrue(self.context(45, "hi").startswith("Context ~45%. Repo work: /handoff:close"))
        self.assertIn("/compact Keep:", self.context(45, "again"))
        self.assertIsNone(self.context(45), "the prompt told step 40")
        self.assertIn("~45%", self.context(45, "/handoff:away"), "away does not latch")

    def test_context_mid_run_steps(self):
        self.assertIsNotNone(self.context(41))
        self.assertIsNone(self.context(48))
        self.assertIn("~50%", self.context(50))
        self.assertIsNone(self.context(55))
        self.assertIn("~63%", self.context(63))
        self.assertIsNone(self.context(70))

    def test_context_quiet_from_a_close_until_the_next_prompt(self):
        self.assertIsNotNone(self.context(41))
        skill = {"tool_name": "Skill", "tool_input": {"skill": "handoff:close", "args": "spawn"}}
        self.assertIsNone(self.context(52, calls=[skill]))
        self.assertIsNone(self.context(61))
        self.assertIsNotNone(self.context(61, "next request"), "a close does not silence the prompt line")
        self.assertIsNone(self.context(62), "resumes at the step reached")
        self.assertIsNone(self.context(62, "/handoff:close spawn"))
        self.assertIsNone(self.context(65))

    def test_context_low_reading_clears_missing_does_not(self):
        self.assertIsNotNone(self.context(41))
        self.assertIsNone(self.context("missing"))
        self.assertIsNone(self.context(None))
        self.assertIsNone(self.context(42))
        self.assertIsNone(self.context(36))
        self.assertIsNone(self.context(42))
        self.assertIsNone(self.context(35))
        self.assertIsNotNone(self.context(42), "a reading of 35% or less clears the steps")

    def test_context_silent_for_subagents_reporters_and_cold_sessions(self):
        self.assertIsNone(self.context(50, "hi", agent_id="a1"))
        self.assertIsNone(self.context(50, "hi", name="story-report"))
        self.assertIsNone(self.context(50, "hi", requests=29))
        self.assertIsNotNone(self.context(50, "hi", name="story-coordinator"))
        self.assertIsNone(self.context("missing", "hi", sid="nostats"))

    SID = "5e55f00d-0000-0000-0000-000000000000"

    def retry_setup(self, autoretry="on", away=True, status="idle", story="", screen="│ > \n", in_mode="0"):
        """A live fake session (a sleep) in pane %7 with its sessions file and transcript; the tmux stub
        answers show-options with the story, capture-pane with the screen, and records the rest."""
        if getattr(self, "proc", None):  # the previous case's session ends, and its waiter with it
            self.proc.kill()
            self.proc.wait()
        self.proc = subprocess.Popen(["sleep", "60"])
        self.addCleanup(lambda: (self.proc.kill(), self.proc.wait()))
        self.pid = self.proc.pid
        self.calls.unlink(missing_ok=True)
        (self.tmp / "claude" / "sessions").mkdir(exist_ok=True)
        (self.tmp / "claude" / "sessions" / f"{self.pid}.json").write_text(json.dumps(
            {"sessionId": self.SID, "name": "s4", "kind": "interactive", "status": status}))
        self.transcript = self.tmp / "t.jsonl"
        self.transcript.write_text("{}\n")
        (self.tmp / "flowkit").mkdir(exist_ok=True)
        (self.tmp / "flowkit" / "handoff.conf").write_text(f"autoretry={autoretry}\n")
        if away:
            self.flag.write_text("2026-10-20 01:00\n")
        (self.tmp / "story").write_text(story)
        (self.tmp / "screen").write_text(screen)
        stub = self.tmp / "bin" / "tmux"
        stub.write_text(f"#!/bin/sh\ncase \"$1\" in list-panes) echo '{self.pid} %7 {in_mode}';;\n"
                        f"show-options) cat {self.tmp / 'story'};; capture-pane) cat {self.tmp / 'screen'};;\n"
                        f"*) echo tmux \"$@\" >> {self.calls};; esac\n")
        stub.chmod(0o755)
        self.env.update(CLAUDE_PID=str(self.pid), TMUX_PANE="%7", XDG_CONFIG_HOME=str(self.tmp),
                        FLOWKIT_RETRY_MINUTE="0.05")

    def api_error(self, error, details=""):
        return self.handoff("api-error", stdin=json.dumps({
            "session_id": self.SID, "hook_event_name": "StopFailure", "error": error, "error_details": details,
            "transcript_path": str(self.transcript)}))

    def record(self):
        path = self.tmp / "claude" / "flowkit" / "retry" / f"{self.SID}.json"
        return json.loads(path.read_text()) if path.exists() else None

    def typed(self, wait=3.0):
        """What the waiter typed into %7 within `wait` seconds, once its lock is gone."""
        deadline = time.time() + wait
        while time.time() < deadline:
            text = self.calls.read_text() if self.calls.exists() else ""
            if "send-keys" in text and not list((self.tmp / "claude" / "flowkit" / "retry").glob("*.lock")):
                return text
            time.sleep(0.05)
        return self.calls.read_text() if self.calls.exists() else ""

    def test_retry_record_lifecycle_and_log(self):
        self.retry_setup(autoretry="off")
        self.api_error("server_error", "500  Internal\nerror")
        rec = self.record()
        self.assertEqual((rec["kind"], rec["attempts"], rec["name"], rec["pid"]), ("server_error", 0, "s4", self.pid))
        first = rec["first"]
        self.api_error("overloaded")
        rec = self.record()
        self.assertEqual((rec["kind"], rec["first"]), ("overloaded", first), "a repeat error keeps the first time")
        log = (self.tmp / "claude" / "flowkit" / "retries.log").read_text().splitlines()
        self.assertRegex(log[0], r"^> retry s4 · \d{4}-\d\d-\d\d \d\d:\d\d · server_error · 500 Internal error$")
        self.assertTrue(log[1].endswith("· overloaded"))
        self.handoff("api-ok", stdin=json.dumps({"session_id": self.SID, "hook_event_name": "Stop"}))
        self.assertIsNone(self.record())
        self.assertFalse(self.calls.exists(), "autoretry=off types nothing")

    def test_retry_line_goes_to_the_story_file(self):
        self.retry_setup(autoretry="off", story="my-story\n")
        story = self.tmp / "claude" / "stories" / "my-story.md"
        story.parent.mkdir()
        story.write_text("# my-story\n")
        self.api_error("authentication_failed", "invalid x-api-key")
        self.assertIn("· authentication_failed · invalid x-api-key", story.read_text().splitlines()[-1])
        self.assertFalse((self.tmp / "claude" / "flowkit" / "retries.log").exists())

    def test_retry_continues_server_errors_while_away(self):
        for error in ("server_error", "overloaded"):
            self.retry_setup()
            self.api_error(error)
            self.assertIn(f"tmux send-keys -t %7 -l Your last turn ended on an API error ({error}) at ", self.typed())
            self.assertIn("Carry on from where you were. ; run-shell sleep 0.5 ; send-keys -t %7 Enter", self.typed())
            self.assertEqual(self.record()["attempts"], 1)
            self.handoff("api-ok", stdin=json.dumps({"session_id": self.SID}))

    def test_retry_max_output_tokens_once_in_smaller_pieces(self):
        self.retry_setup()
        self.api_error("max_output_tokens")
        self.assertIn("in smaller pieces", self.typed())
        self.calls.unlink()
        self.api_error("max_output_tokens")
        self.assertEqual(self.typed(0.5), "", "one continue per record")

    def test_retry_leaves_the_rest_alone(self):
        for kw, error in ((dict(), "authentication_failed"), (dict(), "invalid_request"),
                          (dict(), "unknown"), (dict(away=False), "server_error"),
                          (dict(autoretry="off"), "server_error")):
            self.retry_setup(**kw)
            self.api_error(error)
            self.assertEqual(self.typed(0.5), "", f"{kw} {error}")
            self.assertIsNotNone(self.record())
            self.flag.unlink(missing_ok=True)

    def test_retry_awake_counts_like_away(self):
        self.retry_setup(away=False)
        (self.tmp / "claude" / "awake").write_text("2026-10-20 01:00\n")
        self.api_error("server_error")
        self.assertIn("send-keys", self.typed())

    def test_retry_counts_the_away_flag_from_its_write(self):
        self.retry_setup(away=False)
        self.set_flag(time.time() + 240, by="coord-a")
        self.assertIsNone(self.gate(), "not in effect: questions still go through")
        self.api_error("server_error")
        self.assertIn("send-keys", self.typed(), "set five minutes ahead: the night has begun")

    def test_retry_types_nothing_unless_safe(self):
        for kw in (dict(status="busy"), dict(screen="│ > half a thought\n"), dict(in_mode="1")):
            self.retry_setup(**kw)
            self.api_error("server_error")
            self.assertEqual(self.typed(0.6), "", kw)
        self.retry_setup()
        self.env["FLOWKIT_RETRY_MINUTE"] = "0.5"  # the first continue is due after 1 s
        self.api_error("server_error")
        time.sleep(0.4)
        with self.transcript.open("a") as f:
            f.write("{}\n")
        self.assertEqual(self.typed(1.5), "", "the transcript moved")

    def test_retry_one_waiter_per_session_and_pid(self):
        self.retry_setup()
        lock = self.tmp / "claude" / "flowkit" / "retry" / f"{self.SID}-{self.pid}.lock"
        lock.parent.mkdir(parents=True)
        lock.write_text(str(self.pid))  # held by a live process
        self.api_error("server_error")
        self.assertEqual(self.typed(0.6), "")
        lock.write_text("999999")  # a dead waiter's lock is taken over
        self.api_error("server_error")
        self.assertIn("send-keys", self.typed())

    @unittest.skipUnless(shutil.which("tmux"), "needs tmux")
    def test_retry_types_into_a_real_pane_but_not_in_copy_mode(self):
        """A throwaway tmux server whose pane runs `cat`, standing in for the session's process."""
        sock = f"flowkit-retry-{os.getpid()}"
        real = lambda *a: subprocess.run(["tmux", "-L", sock, *a], capture_output=True, text=True)
        self.addCleanup(real, "kill-server")
        real("-f", "/dev/null", "new-session", "-d", "-x", "200", "-y", "20", "cat")
        cat = int(real("list-panes", "-F", "#{pane_pid}").stdout.split()[0])
        for copy_mode in (True, False):
            self.retry_setup()
            (self.tmp / "bin" / "tmux").unlink()
            (self.tmp / "claude" / "sessions" / f"{cat}.json").write_text(json.dumps(
                {"sessionId": self.SID, "name": "s4", "kind": "interactive", "status": "idle"}))
            self.env.update(CLAUDE_PID=str(cat), FLOWKIT_TMUX_SOCKET=sock, TMUX_PANE="")
            if copy_mode:
                real("copy-mode")
            self.api_error("server_error")
            time.sleep(1.0)
            screen = real("capture-pane", "-p").stdout
            if copy_mode:
                self.assertNotIn("API error", screen)
                real("send-keys", "-X", "cancel")
                self.handoff("api-ok", stdin=json.dumps({"session_id": self.SID}))
            else:
                self.assertIn("Your last turn ended on an API error (server_error)", screen)

    def test_retry_background_session_is_resumed(self):
        self.retry_setup()
        (self.tmp / "claude" / "sessions" / f"{self.pid}.json").write_text(json.dumps(
            {"sessionId": self.SID, "name": "bg", "kind": "background", "status": "idle"}))
        self.agents([{"name": "bg", "kind": "background", "id": "b1", "sessionId": self.SID, "status": "idle"}])
        self.api_error("overloaded")
        deadline = time.time() + 3
        while time.time() < deadline and not self.calls.exists():
            time.sleep(0.05)
        time.sleep(0.2)
        self.assertIn(f"--bg --resume {self.SID} Your last turn ended on an API error (overloaded)",
                      self.calls.read_text())

    def iso(self, when):
        return datetime.fromtimestamp(when, timezone.utc).isoformat().replace("+00:00", "Z")

    def line(self, kind, text, later=1, **extra):
        """A transcript line stamped `later` seconds after the limit line."""
        return {"type": kind, "timestamp": self.iso(self.limit_at + later), "message": {"content": text}, **extra}

    def limit_setup(self, reset=None, text="You've hit your weekly limit", model="claude-fable-5-2", after=(),
                    conf="", settings=None, remote=True, **kw):
        """retry_setup with no autoretry and no away flag, and a transcript that ends as a real one does
        on a usage limit: a model's reply, the limit line (with its quota answer when `reset` is given),
        the harness's own unstamped lines, then `after`."""
        self.retry_setup(autoretry="off", away=False, **kw)
        retry = self.tmp / "claude" / "flowkit" / "retry"
        (retry / f"{self.SID}.json").unlink(missing_ok=True)  # the previous case's waiter goes with its record
        deadline = time.time() + 3
        while list(retry.glob("*.lock")) and time.time() < deadline:
            time.sleep(0.02)
        for path in (self.tmp / "claude" / "flowkit" / "retries.log", self.tmp / "claude" / "settings.json",
                     self.tmp / "usage.jsonl", self.tmp / "probes", self.tmp / "holds", self.calls):
            path.unlink(missing_ok=True)
        stub = self.tmp / "bin" / "caffeinate"
        stub.write_text(f"#!/bin/sh\necho \"$@\" >> {self.tmp / 'holds'}\nexec sleep 5\n")
        stub.chmod(0o755)
        (self.tmp / "flowkit" / "handoff.conf").write_text(conf)
        if settings is not None:
            (self.tmp / "claude" / "settings.json").write_text(json.dumps(settings))
        (self.tmp / "claude" / "sessions" / f"{self.pid}.json").write_text(json.dumps(
            {"sessionId": self.SID, "name": "s4", "kind": "interactive", "status": kw.get("status", "idle"),
             **({"bridgeSessionId": "session_x"} if remote else {})}))
        self.limit_at = time.time()
        limit = {"type": "assistant", "timestamp": self.iso(self.limit_at), "isApiErrorMessage": True,
                 "error": "rate_limit", "message": {"model": "<synthetic>", "content": [{"type": "text", "text": text}]}}
        if reset is not None:
            limit["quotaLimits"] = {"status": "rejected", "resetsAt": reset, "rateLimitType": "seven_day"}
        rows = [{"type": "assistant", "timestamp": self.iso(self.limit_at - 5), "message": {"model": model, "content": "ok"}},
                limit, {"type": "custom-title", "customTitle": "s4"}, {"type": "mode", "mode": "normal"}, *after]
        self.transcript.write_text("".join(json.dumps(row) + "\n" for row in rows))

    def log(self):
        path = self.tmp / "claude" / "flowkit" / "retries.log"
        return path.read_text().splitlines() if path.exists() else []

    def sent(self, wait=3.0):
        """What a limit waiter typed into %7 within `wait` seconds; it lives on, so its lock stays."""
        deadline = time.time() + wait
        while time.time() < deadline:
            text = self.calls.read_text() if self.calls.exists() else ""
            if "Enter" in text:
                return text
            time.sleep(0.02)
        return self.calls.read_text() if self.calls.exists() else ""

    def rec_when(self, test, wait=4.0):
        deadline = time.time() + wait
        while time.time() < deadline:
            rec = self.record()
            if rec and test(rec):
                return rec
            time.sleep(0.02)
        self.fail(f"the record never got there: {self.record()}")

    def buckets(self, week=(49, 7200), five_hour=(9, 3600), **models):
        """A usage answer: (percent, seconds until the reset) for the week, the 5 hours and each model's
        bucket by display name. Returns the second the resets are counted from."""
        base = int(time.time())
        stamp = lambda secs: datetime.fromtimestamp(base + secs, timezone.utc).isoformat()
        limits = [{"kind": "session", "percent": five_hour[0], "resets_at": stamp(five_hour[1]), "scope": None},
                  {"kind": "weekly_all", "percent": week[0], "resets_at": stamp(week[1]), "scope": None}]
        limits += [{"kind": "weekly_scoped", "percent": pct, "resets_at": stamp(secs),
                    "scope": {"model": {"id": None, "display_name": name}, "surface": None}}
                   for name, (pct, secs) in models.items()]
        answer = {"type": "control_response", "response": {
            "subtype": "success", "request_id": "flowkit-usage", "response": {"rate_limits": {"limits": limits}}}}
        (self.tmp / "usage.jsonl").write_text(json.dumps(answer) + "\n")
        return base

    def test_limit_resumes_present_or_away_without_a_conf(self):
        reset = time.time() + 3.0  # its waiter is up well before the last "30 minutes" (1.5 s) begin
        self.limit_setup(reset=reset)
        self.api_error("rate_limit")
        typed = self.sent(6.0)
        self.assertIn("tmux send-keys -t %7 -l Your last turn stopped on the usage limit at ", typed)
        self.assertIn("; it has reset. Carry on from where you were. ; run-shell sleep 0.5 ; send-keys -t %7 Enter", typed)
        rec = self.rec_when(lambda r: r.get("state") == "continued")
        self.assertEqual((rec["continues"], rec["continued_for"], rec["reset"], rec["handled"]), (1, reset, reset, rec["at"]))
        self.assertGreaterEqual(rec["continued_at"], reset + 3 * 0.05, "three minutes after the reset")
        self.assertEqual((rec["status"], rec["screen"], rec["rest"]),
                         ("idle", ["│ > "], {"status": "idle", "screen": ["│ > "]}), "what it looked like, kept")
        self.assertEqual(self.probes(), 0, "the limit line names its reset: no usage call")
        self.assertEqual((self.tmp / "holds").read_text(), f"-i -w {self.pid}\n", "kept awake for the last 30 minutes")
        log = self.log()
        self.assertRegex(log[0], r"^> retry s4 · \d{4}-\d\d-\d\d \d\d:\d\d · rate_limit$")
        self.assertRegex(log[1], r"^> limit s4 · \d{4}-\d\d-\d\d \d\d:\d\d · continues at \d{4}-\d\d-\d\d \d\d:\d\d, "
                                 r"once the limit has reset: the computer must be awake then$")
        self.assertEqual(len(log), 2)

    def test_limit_resume_off_by_the_settings_or_the_conf(self):
        for kw in (dict(settings={"autoContinueAtUsageLimit": False}), dict(conf="limitresume=off\n")):
            self.limit_setup(reset=time.time() + 0.2, **kw)
            self.api_error("rate_limit")
            self.assertEqual(self.sent(0.8), "", kw)
            rec = self.record()
            self.assertEqual((rec["kind"], rec["resume"], "due" in rec), ("rate_limit", False, False), kw)
        self.limit_setup(reset=time.time() + 2.5)
        self.api_error("rate_limit")
        self.rec_when(lambda r: r.get("due"))
        (self.tmp / "flowkit" / "handoff.conf").write_text("limitresume=off\n")
        self.assertEqual(self.sent(3.2), "", "turned off while its waiter waits")
        self.assertFalse(self.record()["resume"])

    def test_limit_without_remote_control_is_claude_codes_own_within_a_day(self):
        on = {"autoContinueAtUsageLimit": True}
        self.limit_setup(reset=time.time() + 0.2, remote=False, settings=on)
        self.api_error("rate_limit")
        rec = self.rec_when(lambda r: r.get("handled"))
        self.assertEqual((rec["state"], rec["remote"], self.sent(0.6)), ("native", False, ""))
        far = time.time() + 25 * 3600
        self.limit_setup(reset=far, remote=False, settings=on)
        self.api_error("rate_limit")
        rec = self.rec_when(lambda r: r.get("due"))
        self.assertEqual(rec["state"], "waiting", "a reset more than 24 hours away is not")
        self.assertAlmostEqual(rec["due"], far + 3 * 0.05)
        self.limit_setup(reset=time.time() + 0.2, remote=False)
        self.api_error("rate_limit")
        self.assertIn("usage limit", self.sent(), "nor any reset while the settings do not turn its wait on")

    def test_limit_due_from_a_live_reading_per_bucket_and_model(self):
        capped = dict(week=(100, 7200), five_hour=(100, 3600), Fable=(100, 10800), Opus=(100, 18000))
        for model, kw, secs in (("claude-fable-5-2", capped, 10800),  # its own bucket, not the other model's
                                ("claude-haiku-5-5", capped, 18000),  # no bucket by its name: every capped one
                                ("claude-fable-5-2", dict(week=(100, 7200), Fable=(37, 10800)), 7200),
                                ("claude-fable-5-2", dict(week=(99, 7200), five_hour=(100, 3600)), 3600)):
            self.limit_setup(model=model)
            base = self.buckets(**kw)
            self.api_error("rate_limit")
            rec = self.rec_when(lambda r: r.get("due"))
            self.assertEqual((rec["reset"], self.probes()), (base + secs, 1), (model, kw))
            self.assertAlmostEqual(rec["due"], base + secs + 3 * 0.05)
        time.sleep(0.3)
        self.assertFalse((self.tmp / "holds").exists(), "hours before the reset the computer is not held awake")

    def test_limit_refuses_a_cached_an_old_and_a_passed_reading(self):
        self.limit_setup()
        self.buckets(week=(100, 7200))
        self.assertEqual(self.handoff("usage").returncode, 0)  # the 10-minute cache now holds that answer
        base = self.buckets(week=(100, 9000))
        self.api_error("rate_limit")
        rec = self.rec_when(lambda r: r.get("due"))
        self.assertEqual((rec["reset"], self.probes()), (base + 9000, 2), "asked again, the cache not read")
        self.limit_setup()
        weeks = self.tmp / "claude" / "usage-stats"
        weeks.mkdir(exist_ok=True)
        (weeks / "week-1.json").write_text(json.dumps({"seven_day_pct": 100, "resets_at": int(time.time()) + 7200}))
        self.api_error("rate_limit")
        rec = self.rec_when(lambda r: r.get("due"))
        self.assertEqual((rec["reset"], self.probes()), (None, 1), "old figures name no reset")
        self.assertIn("usage limit", self.sent(), "so it is tried like an API error: after 2 minutes")
        self.limit_setup()
        self.buckets(week=(100, -60))
        self.api_error("rate_limit")
        self.assertIsNone(self.rec_when(lambda r: r.get("due"))["reset"], "a reset that has passed")

    def test_limit_reads_the_reset_from_the_message(self):
        self.limit_setup(text="You've hit your weekly limit · resets 2am (Europe/Berlin)")
        self.api_error("rate_limit")
        rec = self.rec_when(lambda r: r.get("due"))
        at = datetime.fromtimestamp(self.limit_at, ZoneInfo("Europe/Berlin"))
        two = at.replace(hour=2, minute=0, second=0, microsecond=0)
        self.assertEqual(rec["reset"], (two if two > at else two + timedelta(days=1)).timestamp())
        loader = importlib.machinery.SourceFileLoader("flowkit_handoff", str(HANDOFF))
        handoff = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(handoff)
        berlin, new_york = ZoneInfo("Europe/Berlin"), ZoneInfo("America/New_York")
        base = datetime(2026, 11, 17, 23, 54, tzinfo=berlin)
        for text, want in (("resets 2am (Europe/Berlin)", datetime(2026, 11, 18, 2, 0, tzinfo=berlin)),
                           ("resets 11:30pm (Europe/Berlin)", datetime(2026, 11, 18, 23, 30, tzinfo=berlin)),
                           ("resets 12am (Europe/Berlin)", datetime(2026, 11, 18, 0, 0, tzinfo=berlin)),
                           ("resets 14:00 (Europe/Berlin)", datetime(2026, 11, 18, 14, 0, tzinfo=berlin)),
                           ("resets Nov 25, 2am (Europe/Berlin)", datetime(2026, 11, 25, 2, 0, tzinfo=berlin)),
                           ("resets Nov 25 at 2:30pm (America/New_York)", datetime(2026, 11, 25, 14, 30, tzinfo=new_york)),
                           ("resets Jan 3, 9am (Europe/Berlin)", datetime(2027, 1, 3, 9, 0, tzinfo=berlin)),
                           ("resets soon", None), ("resets 2", None), ("resets Foo 3, 9am", None), ("try again later", None)):
            self.assertEqual(handoff.spoken_reset(f"You've hit your limit · {text}", base.timestamp()),
                             want and want.timestamp(), text)
        local = handoff.spoken_reset("resets 2am (No/Such_Zone)", base.timestamp())
        self.assertEqual(datetime.fromtimestamp(local).strftime("%H:%M"), "02:00", "an unknown zone: the computer's")

    def test_limit_a_prompt_or_a_reply_cancels_metadata_and_commands_do_not(self):
        self.limit_at = time.time()
        command = [{"type": "file-history-snapshot"},
                   self.line("user", "<local-command-caveat>The command below was run directly</local-command-caveat>", isMeta=True),
                   self.line("user", "<command-name>/usage</command-name>"),
                   self.line("system", "", subtype="local_command"),
                   self.line("user", "<local-command-stdout>Week 100% used</local-command-stdout>"),
                   {"type": "bridge-session", "bridgeSessionId": "session_x"}]
        self.limit_setup(reset=time.time() + 0.2, after=command)
        self.api_error("rate_limit")
        self.assertIn("usage limit", self.sent(), "the harness's lines and a /usage are no turn")
        for row in (("user", "do the other thing first"), ("assistant", "On it.")):
            self.limit_setup(reset=time.time() + 0.2)
            with self.transcript.open("a") as f:
                f.write(json.dumps(self.line(*row)) + "\n")
            self.api_error("rate_limit")
            rec = self.rec_when(lambda r: r.get("handled"))
            self.assertEqual((rec["state"], self.sent(0.5)), ("moved", ""), row)

    def test_limit_types_only_into_a_free_prompt_box(self):
        menu = "│ > \n  1. Stop and wait for the limit to reset\n  2. Switch to extra usage\n  Enter to confirm · Esc to cancel\n"
        self.limit_setup(reset=time.time() + 0.2, screen=menu)
        self.api_error("rate_limit")
        self.rec_when(lambda r: r.get("state") == "dialog")
        time.sleep(1.8)  # the next slot, 30 minutes on, finds it still open
        self.assertEqual(self.sent(0.1), "")
        said = [l for l in self.log() if "not continued" in l]
        self.assertEqual(len(said), 1, said)
        self.assertRegex(said[0], r"^> limit s4 · .* · not continued: a dialog is open below its prompt box; looked at "
                                  r"again every 30 minutes$")
        quoted = ("  ❯ 1. Yes\n  Enter to confirm · Esc to cancel\n\n────\n❯ \n────\n"
                  "  main   Model high\n  ⏵⏵ auto mode on (shift+tab to cycle)\n")
        self.limit_setup(reset=time.time() + 0.2, screen=quoted)
        self.api_error("rate_limit")
        self.assertIn("usage limit", self.sent(), "a dialog quoted above the prompt box is none")
        for kw, state in ((dict(screen="│ > half a thought\n"), "draft"), (dict(screen="  1. Yes\n  2. No\n"), "no prompt box"),
                          (dict(in_mode="1"), "copy mode"), (dict(status="busy"), "not idle")):
            self.limit_setup(reset=time.time() + 0.2, **kw)
            self.api_error("rate_limit")
            rec = self.rec_when(lambda r: r.get("state") == state)
            self.assertEqual((self.sent(0.3), rec.get("handled")), ("", None), f"{kw}: the next slot")
        self.limit_setup(reset=time.time() + 0.2)
        stub = self.tmp / "bin" / "tmux"
        stub.write_text(stub.read_text().replace(f"'{self.pid} %7", "'999999 %7"))
        self.api_error("rate_limit")
        rec = self.rec_when(lambda r: r.get("handled"))
        self.assertEqual((rec["state"], self.sent(0.3)), ("unreachable", ""))
        self.assertIn("· not continued: it does not run in a tmux pane; it waits for you", self.log()[-1])

    def test_limit_with_no_reset_known_is_tried_like_an_api_error_three_times(self):
        self.limit_setup(text="API Error: 429 rate_limit_error")
        for n in (1, 2, 3):
            self.api_error("rate_limit")
            self.assertIn("usage limit", self.sent())
            rec = self.rec_when(lambda r: r.get("continues") == n)
            self.assertEqual((rec["reset"], rec["state"]), (None, "continued"))
            self.calls.unlink()
        self.api_error("rate_limit")
        rec = self.rec_when(lambda r: r.get("state") == "unreachable")
        self.assertEqual((rec["handled"], self.sent(0.8)), (rec["at"], ""))
        self.assertEqual([l.split(" · ", 2)[2] for l in self.log()],
                         ["rate_limit", "not continued: 3 continues ended on the limit again; it waits for you"])

    def test_limit_one_waiter_and_one_continue_however_many_are_started(self):
        self.limit_setup(reset=time.time() + 0.8)
        self.api_error("rate_limit")
        extra = [subprocess.Popen(["python3", str(HANDOFF), "retry-wait", self.SID], env=self.env) for _ in range(2)]
        self.addCleanup(lambda: [(p.kill(), p.wait()) for p in extra])
        self.assertIn("usage limit", self.sent())
        self.rec_when(lambda r: r.get("state") == "continued")
        lock = self.tmp / "claude" / "flowkit" / "retry" / f"{self.SID}.lock"
        os.kill(int(lock.read_text()), signal.SIGKILL)  # the waiter dies; its lock file stays, unlocked
        extra.append(subprocess.Popen(["python3", str(HANDOFF), "retry-wait", self.SID], env=self.env))
        time.sleep(1.0)
        self.assertEqual(self.calls.read_text().count("-l Your last turn"), 1, "a continue is typed once")
        self.assertTrue(lock.exists(), "the new waiter stays beside the record")
        self.api_error("rate_limit")  # woken while limited: the stop is noted, without another line
        self.assertEqual(len([l for l in self.log() if l.startswith("> retry")]), 1)

    NOTICE = ("Remote Control disconnected \u2014 signed-in claude.ai account or organization changed on this machine "
              "\u2014 run /remote-control to start a session for the current account")

    def notice(self):
        """The line a session logs when its login changed under it, appended to the transcript."""
        with self.transcript.open("a") as f:
            f.write(json.dumps({"type": "system", "subtype": "informational", "level": "warning",
                                "timestamp": self.iso(time.time()), "content": self.NOTICE}) + "\n")

    def unlink_remote(self):
        """The session's Remote Control link goes, as it does when its login changes."""
        path = self.tmp / "claude" / "sessions" / f"{self.pid}.json"
        path.write_text(json.dumps({k: v for k, v in json.loads(path.read_text()).items() if k != "bridgeSessionId"}))

    def module(self):
        loader = importlib.machinery.SourceFileLoader("flowkit_handoff", str(HANDOFF))
        handoff = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(handoff)
        return handoff

    def test_limit_record_keeps_its_login_and_a_waiter_started_elsewhere_asks_as_it(self):
        plain, folder, spelled = self.two_logins()
        stub = self.tmp / "bin" / "claude"
        stub.write_text(stub.read_text().replace("echo probe >>", 'echo "probe ${CLAUDE_CONFIG_DIR-unset}" >>'))
        self.limit_setup()  # no quota answer in the limit line: a reading is asked for
        del self.env["CLAUDE_CONFIG_DIR"]
        base = self.buckets(week=(100, 7200))
        self.api_error("rate_limit")
        rec = self.rec_when(lambda r: r.get("due"))
        self.assertEqual((rec["config"], rec["account"], rec["reset"]), (None, plain, base + 7200))
        lock = self.tmp / "claude" / "flowkit" / "retry" / f"{self.SID}.lock"
        os.kill(int(lock.read_text()), signal.SIGKILL)  # its waiter dies; the tick starts one, in its own environment
        path = self.tmp / "claude" / "flowkit" / "retry" / f"{self.SID}.json"
        path.write_text(json.dumps({**rec, "at": rec["at"] + 1, "due": None, "reset": None}))
        base = self.buckets(week=(100, 9000))
        tick = subprocess.Popen(["python3", str(HANDOFF), "retry-wait", self.SID], env={**self.env, "CLAUDE_CONFIG_DIR": spelled})
        self.addCleanup(lambda: (tick.kill(), tick.wait()))
        rec = self.rec_when(lambda r: r.get("due"))
        self.assertEqual((rec["reset"], (self.tmp / "probes").read_text().splitlines()), (base + 9000, ["probe unset"] * 2),
                         "asked as the login the session stopped on, not as the other one on this folder")
        self.limit_setup()
        self.env["CLAUDE_CONFIG_DIR"] = spelled
        self.api_error("rate_limit")
        rec = self.rec_when(lambda r: r.get("due"))
        self.assertEqual((rec["config"], rec["account"]), (spelled, folder))
        del self.env["CLAUDE_CONFIG_DIR"]
        self.api_error("rate_limit")
        rec = self.record()
        self.assertEqual((rec["config"], rec["account"]), (spelled, folder), "one process, one login: kept as first read")
        path = self.tmp / "claude" / "flowkit" / "retry" / f"{self.SID}.json"
        path.write_text(json.dumps({**rec, "pid": 999999}))
        self.api_error("rate_limit")
        rec = self.record()
        self.assertEqual((rec["config"], rec["account"]), (None, plain), "resumed in a new process, on the other login")

    def test_limit_never_takes_a_reset_from_another_accounts_reading(self):
        one = self.login("acct-1")
        self.limit_setup()
        self.env["FLOWKIT_RETRY_MINUTE"] = "6"  # the stop is planned a second after it
        self.buckets(week=(100, 7200))
        self.api_error("rate_limit")
        self.login("acct-2")  # the file alone: the session's Remote Control link stands, so it is no change of its own
        rec = self.rec_when(lambda r: r.get("due"), 6.0)
        self.assertEqual((rec["account"], rec["reset"], self.probes()), (one, None, 1),
                         "the other account's capped week names no reset for this session")
        self.assertEqual(rec["due"], rec["at"] + 2 * 6, "so it is tried like an API error")
        self.login("acct-1")
        self.limit_setup()
        base = self.buckets(week=(100, 7200))
        self.api_error("rate_limit")
        self.assertEqual(self.rec_when(lambda r: r.get("due"))["reset"], base + 7200, "its own account's reading is taken")

    def test_limit_an_account_change_is_a_reset(self):
        one = self.login("acct-1")
        self.limit_setup(reset=time.time() + 3600)
        self.api_error("rate_limit")
        rec = self.rec_when(lambda r: r.get("due"))
        self.assertEqual((rec["config"], rec["account"]), (str(self.tmp / "claude"), one))
        two = self.login("acct-2")
        self.buckets()
        self.assertEqual(self.sent(0.6), "", "the file alone is no change: the session's Remote Control link stands")
        self.assertEqual(self.record()["account"], one)
        self.unlink_remote()
        typed = self.sent()
        self.assertIn("tmux send-keys -t %7 -l Your last turn stopped on the usage limit at ", typed)
        self.assertIn("; the account it runs on has changed since. Carry on from where you were. ; run-shell", typed)
        rec = self.rec_when(lambda r: r.get("state") == "continued")
        self.assertEqual((rec["account"], rec["continues"], rec["reset"], self.probes()), (two, 1, None, 1))
        self.assertRegex(self.log()[-1], r"^> limit s4 · .* · the account it runs on changed: counted as a reset$")
        time.sleep(0.5)
        self.assertEqual(self.calls.read_text().count("-l Your last turn"), 1, "one continue")

        self.login("acct-1")  # the new account is at its limit too: its reset is waited for
        self.limit_setup(reset=time.time() + 3600, remote=False)
        self.api_error("rate_limit")
        self.rec_when(lambda r: r.get("due"))
        base = self.buckets(week=(100, 7200))
        self.login("acct-2")
        rec = self.rec_when(lambda r: r.get("switched") and r.get("due"))
        self.assertEqual((rec["reset"], rec["state"], self.sent(0.4)), (base + 7200, "waiting", ""))
        self.assertAlmostEqual(rec["due"], base + 7200 + 3 * 0.05)

        self.login("acct-1")  # a link that came back before anyone looked: the transcript still says it dropped
        self.limit_setup(reset=time.time() + 3600)
        self.api_error("rate_limit")
        self.rec_when(lambda r: r.get("due"))
        self.buckets()
        self.login("acct-1", "org-2")
        self.notice()
        self.assertIn("has changed since", self.sent(), "another organization is another account")

        self.login("acct-1")  # left to Claude Code's own wait, which ends at the change without continuing
        self.limit_setup(reset=time.time() + 600, remote=False, settings={"autoContinueAtUsageLimit": True})
        self.api_error("rate_limit")
        self.assertEqual(self.rec_when(lambda r: r.get("handled"))["state"], "native")
        self.buckets()
        self.login("acct-2")
        self.assertIn("has changed since", self.sent())
        self.assertEqual(self.rec_when(lambda r: r.get("state") == "continued")["continues"], 1)

    def test_limit_an_account_change_brings_remote_control_back_before_the_continue(self):
        self.login("acct-1")
        self.limit_setup(reset=time.time() + 3600)
        kept = self.tmp / "claude" / "projects" / "-repo" / f"{self.SID}.jsonl"
        kept.parent.mkdir(parents=True)
        self.transcript = Path(shutil.move(self.transcript, kept))
        self.api_error("rate_limit")
        self.rec_when(lambda r: r.get("due"))
        self.buckets()
        self.notice()
        self.unlink_remote()
        self.login("acct-2")
        self.rec_when(lambda r: r.get("state") == "continued")
        typed = self.calls.read_text()
        self.assertEqual((typed.count("-l /remote-control ;"), typed.count("-l Your last turn stopped")), (1, 1))
        self.assertLess(typed.index("/remote-control"), typed.index("Your last turn"), "so the turn shows on the phone")

    def test_limit_plan_after_an_account_change_by_the_clock(self):
        handoff = self.module()
        clock, marks, reading = [1000.0], [], [None]
        open_week = {"week": {"percent": 49, "resets_at": 9000}}
        capped = {"week": {"percent": 100, "resets_at": 9000}, "five_hour": {"percent": 100, "resets_at": 5000}}
        rec = {"at": 900.0, "switched": 950.0, "pid": 1, "name": "s4", "session": self.SID, "transcript": "",
               "continues": 3, "continued_for": 9000.0}

        def plan(**change):
            marks.clear()
            return handoff.limit_plan(self.SID, {**rec, **change}, 60)

        with mock.patch.multiple(handoff, limit_pane=lambda rec: None, session_file=lambda *a: {},
                                 usage_reading=lambda rec: reading[0],
                                 limit_mark=lambda sid, key=None, line=None, **fields: marks.append(fields)), \
                mock.patch.object(handoff.time, "time", lambda: clock[0]):
            reading[0] = open_week
            self.assertEqual(plan(), (1000.0, None), "a stop from before the change, the new account open: at once")
            self.assertEqual((marks[0]["continues"], marks[0]["continued_for"]), (0, None), "counted from nothing")
            reading[0] = capped
            self.assertEqual(plan(), (9000 + 3 * 60, 9000), "capped: after the new account's last reset")
            reading[0] = None
            self.assertEqual(plan(), (950.0 + 2 * 60, None), "no reading: tried like an API error")
            reading[0] = open_week
            clock[0] = 1009.5
            self.assertEqual(plan(at=1009.0), (1010.0, None), "a stop 59 s after the change: once the minute is over")
            self.assertEqual(marks[0]["continues"], 0, "and no continue that failed")
            clock[0] = 1012.0
            self.assertIsNone(plan(at=1011.0), "61 s after it, the stop is the new account's own")
            self.assertEqual(marks[0]["state"], "unreachable")
            self.assertEqual(plan(at=1011.0, continues=0, continued_for=None)[0], 1011.0 + 2 * 60)

    def remote_setup(self, notice=True, linked=False, **kw):
        """retry_setup's session with its transcript where Claude Code keeps it: one that had a Remote
        Control link and, with `notice`, logged that an account change took it."""
        self.retry_setup(autoretry="off", away=False, **kw)
        shutil.rmtree(self.tmp / "claude" / "flowkit" / "remote", ignore_errors=True)
        (self.tmp / "claude" / "flowkit" / "retries.log").unlink(missing_ok=True)
        self.transcript = self.tmp / "claude" / "projects" / "-repo" / f"{self.SID}.jsonl"
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        self.transcript.write_text(json.dumps({"type": "bridge-session", "bridgeSessionId": "cse_old"}) + "\n")
        if notice:
            self.notice()
        self.session(status=kw.get("status", "idle"), **({"bridgeSessionId": "cse_old"} if linked else {}))

    def session(self, **fields):
        path = self.tmp / "claude" / "sessions" / f"{self.pid}.json"
        path.write_text(json.dumps({"sessionId": self.SID, "name": "s4", "kind": "interactive", **fields}))

    def remote(self, suffix):
        return self.tmp / "claude" / "flowkit" / "remote" / f"{self.SID}.{suffix}"

    TYPED = "tmux send-keys -t %7 -l /remote-control ; run-shell sleep 0.5 ; send-keys -t %7 Enter\n"

    def test_reconnect_types_remote_control_once_per_notice_into_a_free_idle_session(self):
        self.remote_setup()
        self.handoff("reconnect", self.SID)
        self.assertEqual(self.calls.read_text(), self.TYPED)
        self.assertEqual(json.loads(self.remote("json").read_text())["state"], "failed")
        self.assertRegex(self.log()[-1], r"^> remote s4 · \d{4}-\d\d-\d\d \d\d:\d\d · Remote Control did not come back after "
                                         r"the account changed; type /remote-control in the session$")
        self.handoff("reconnect", self.SID)
        self.handoff("reconnect", "--all")
        self.assertEqual((self.calls.read_text(), len(self.log())), (self.TYPED, 1), "never twice for one notice")
        time.sleep(0.01)
        self.notice()
        self.handoff("reconnect", "--all")
        self.assertEqual(self.calls.read_text(), self.TYPED * 2, "the account changed again")
        with self.transcript.open("a") as f:
            f.write(json.dumps({"type": "system", "subtype": "informational", "timestamp": self.iso(time.time()),
                                "content": "Remote Control disconnected"}) + "\n")
        self.handoff("reconnect", "--all")
        self.assertEqual(self.calls.read_text(), self.TYPED * 2, "a link that went for another reason is the user's")
        for kw, why in ((dict(linked=True), "its link stands"), (dict(notice=False), "it never lost one to an account change")):
            self.remote_setup(**kw)
            self.handoff("reconnect", "--all")
            self.assertFalse(self.calls.exists() or self.remote("owed").exists() or self.remote("json").exists(), why)
        self.remote_setup()
        with self.transcript.open("a") as f:
            f.write(json.dumps({"type": "system", "subtype": "bridge_status", "timestamp": self.iso(time.time() + 1),
                                "content": "/remote-control is active"}) + "\n")
        self.handoff("reconnect", "--all")
        self.assertFalse(self.calls.exists(), "it came back since, and whoever dropped it then meant to")

    def test_reconnect_waits_for_the_turn_end_and_a_free_prompt_box(self):
        menu = "│ > \n  1. Yes\n  2. No\n  Enter to confirm · Esc to cancel\n"
        for kw in (dict(status="busy"), dict(status="waiting"), dict(screen="│ > half a thought\n"), dict(screen=menu),
                   dict(in_mode="1")):
            self.remote_setup(**kw)
            self.handoff("reconnect", "--all")
            self.assertEqual((self.calls.exists(), self.remote("owed").exists()), (False, True), kw)
        self.remote_setup(status="busy")
        self.handoff("reconnect", "--all")
        self.handoff("reconnect")
        self.assertFalse(self.calls.exists(), "still at work")
        self.session(status="idle")
        self.handoff("api-ok", stdin=json.dumps({"session_id": self.SID, "hook_event_name": "Stop"}))
        self.assertEqual(self.sent(), self.TYPED, "its turn ended: the Stop hook starts it")
        self.remote_setup(status="busy")
        self.handoff("reconnect", "--all")
        self.session(status="idle")
        self.handoff("reconnect")
        self.assertEqual(self.calls.read_text(), self.TYPED, "or the tick's next pass, for those owed")
        self.assertFalse(self.remote("owed").exists())
        self.proc.kill()
        self.proc.wait()
        self.handoff("reconnect")
        self.assertFalse(self.remote("json").exists() or self.remote("lock").exists(), "a session that is gone leaves nothing")

    def test_reconnect_sees_the_link_back_or_closes_what_the_command_left_open(self):
        for left in (False, True):
            self.remote_setup()
            self.env["FLOWKIT_RETRY_MINUTE"] = "6"  # the link has 2 s
            run = subprocess.Popen(["python3", str(HANDOFF), "reconnect", self.SID], env=self.env)
            self.addCleanup(lambda: (run.kill(), run.wait()))
            self.assertEqual(self.sent(), self.TYPED)
            if left:
                (self.tmp / "screen").write_text("│ > \n  Remote Control\n  Enter to confirm · Esc to cancel\n")
            else:
                self.session(status="idle", bridgeSessionId="cse_new")
            run.wait(timeout=10)
            state = json.loads(self.remote("json").read_text())["state"]
            if left:
                self.assertEqual((state, self.calls.read_text()), ("failed", self.TYPED + "tmux send-keys -t %7 Escape\n"))
                self.assertIn("did not come back after the account changed: the dialog it left open was closed; type",
                              self.log()[-1])
            else:
                self.assertEqual((state, self.calls.read_text(), self.log()), ("back", self.TYPED, []))

    def test_a_typed_remote_control_is_not_the_user_at_the_keyboard(self):
        self.set_flag(time.time() - 60)
        self.context(10, "first prompt", sid="rc")
        self.context(10, "/remote-control", sid="rc")
        self.assertIsNotNone(self.gate("rc"), "flowkit typed it: the session still decides alone")
        self.context(10, "/remote-control please", sid="rc")
        self.assertIsNone(self.gate("rc"))

    @unittest.skipUnless(shutil.which("tmux"), "needs tmux")
    def test_reconnect_pass_in_real_panes(self):
        """A throwaway tmux server whose panes run tests/fake_session.py: one lost its link to an
        account change, one kept it, one never had one, one lost it while at work."""
        sock = f"flowkit-remote-{os.getpid()}"
        real = lambda *a: subprocess.run(["tmux", "-L", sock, *a], capture_output=True, text=True)
        self.addCleanup(real, "kill-server")
        config = self.tmp / "claude"
        (config / "projects" / "-repo").mkdir(parents=True)
        (self.tmp / "bin" / "tmux").unlink()
        self.env.update(FLOWKIT_TMUX_SOCKET=sock, FLOWKIT_RETRY_MINUTE="6")
        sids = {}
        for n, (name, flags, lost) in enumerate((("lost", "", True), ("kept", "linked", True), ("never", "", False),
                                                 ("working", "busy", True))):
            sids[name] = f"0000000{n}-0000-0000-0000-000000000000"
            fake = f"python3 {PLUGIN / 'tests' / 'fake_session.py'} {config} {sids[name]} {name} {flags}"
            real(*(("-f", "/dev/null", "new-session", "-d", "-x", "120", "-y", "20") if n == 0 else ("split-window", "-d")), fake)
            rows = [{"type": "bridge-session", "bridgeSessionId": "cse_old"}] if name != "never" else []
            if lost:
                rows.append({"type": "system", "subtype": "informational", "timestamp": self.iso(time.time()),
                             "content": self.NOTICE})
            (config / "projects" / "-repo" / f"{sids[name]}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
            real("select-layout", "tiled")
        deadline = time.time() + 10
        while time.time() < deadline and len(list((config / "sessions").glob("*.json"))) < 4:
            time.sleep(0.1)
        typed = lambda name: (config / f"typed-{name}.log").read_text() if (config / f"typed-{name}.log").exists() else ""
        mark = lambda name, suffix: config / "flowkit" / "remote" / f"{sids[name]}.{suffix}"
        self.handoff("reconnect", "--all")
        self.assertEqual([typed(n) for n in ("lost", "kept", "never", "working")], ["/remote-control\n", "", "", ""])
        self.assertEqual(json.loads(mark("lost", "json").read_text())["state"], "back")
        self.assertTrue(mark("working", "owed").exists(), "owed at its turn end")
        pane = {name: pid for name in sids for pid, text in
                ((p.stem, json.loads(p.read_text())) for p in (config / "sessions").glob("*.json")) if text["name"] == name}
        panes = dict(line.split() for line in real("list-panes", "-a", "-F", "#{pane_pid} #{pane_id}").stdout.splitlines())
        self.assertFalse(self.log(), "nothing went wrong")
        real("send-keys", "-t", panes.get(pane["working"], "%3"), "idle", "Enter")
        deadline = time.time() + 5
        while time.time() < deadline and "idle" not in typed("working"):
            time.sleep(0.1)
        self.handoff("api-ok", stdin=json.dumps({"session_id": sids["working"], "hook_event_name": "Stop"}))
        deadline = time.time() + 8
        while time.time() < deadline and "/remote-control" not in typed("working"):
            time.sleep(0.1)
        self.assertEqual(typed("working"), "idle\n/remote-control\n")
        self.handoff("reconnect", "--all")
        self.assertEqual([typed(n) for n in ("lost", "kept", "never")], ["/remote-control\n", "", ""], "once")

    PARKED = "a pane is parked at a start-up prompt: Accessing workspace: (tab demo, pane 2, story demo)"
    LIMIT = "demo-s2 stopped on the usage limit and flowkit cannot continue it (story demo)"
    WAITS = "demo-s5 waits on permission prompt"
    LOGIN = "demo: Login is ready to test on iPhone 15 · Gate: device test"
    COLOURS = "demo: Colours is ready to test on sim · Gate: your look (after the release)"
    RECEIPTS = "side: Receipts is ready to test"

    def idle(self, seconds):
        """A stub `ioreg` whose HID idle time is `seconds`; None: it prints no such line (not a Mac)."""
        stub = self.tmp / "bin" / "ioreg"
        stub.write_text("#!/bin/sh\n" + ("" if seconds is None else f"echo '    | |   \"HIDIdleTime\" = {int(seconds * 1e9)}'\n"))
        stub.chmod(0o755)

    def park(self, now, *panes):
        """flowkit/parked.json as the tick writes it: pane ids parked since 90 s before `now`'s first write."""
        path = self.tmp / "claude" / "flowkit" / "parked.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"at": int(now), "panes": [
            {"pane": p, "tab": "demo", "index": n, "story": "demo", "line": "Accessing workspace:", "since": 1000 + n}
            for n, p in enumerate(panes, 2)]}))

    def limit_record(self, sid, **fields):
        path = self.tmp / "claude" / "flowkit" / "retry" / f"{sid}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"kind": "rate_limit", "state": "unreachable", "at": 100.5, **fields}))

    def remind_world(self, now):
        """Reminders on, nobody at the computer, and one of everything a reminder reads beside what
        it leaves out: a limit stop flowkit still waits on, an API error, a busy session, an item
        that merges on green, one still building, a line of prose."""
        self.env["XDG_CONFIG_HOME"] = str(self.tmp)
        self.handoff("conf", "remind", "on")
        self.idle(3600)
        self.park(now, "%4")
        self.limit_record("s-unreachable", name="demo-s2", story="demo")
        self.limit_record("s-waiting", name="demo-s3", state="waiting")
        self.limit_record("s-error", name="demo-s4", kind="server_error")
        self.agents([{"name": "demo-s5", "sessionId": "s5", "status": "waiting", "waitingFor": "permission prompt"},
                     {"name": "demo-s6", "sessionId": "s6", "status": "busy"}, "noise"])
        self.stories = self.tmp / "claude" / "stories"
        self.stories.mkdir()
        (self.stories / "demo.tests.md").write_text(
            "# demo test queue\n"
            "Login · !1 · iPhone 15 · ready to test ·  · Gate: device test\n"
            "Fonts · !2 · sim · ready to test ·  · Gate: merges on green\n"
            "Colours · !3 · sim · ready to test · Gate: your look (after the release)\n"
            "Maps · !4 · Pixel 8 · building · 15:00 · Gate: device test\n"
            "A sentence that names no field\n")
        (self.stories / "side.tests.md").write_text("Receipts · !9 ·  · ready to test\n")

    def test_remind_dry_run_says_what_waits_and_sends_nothing(self):
        self.remind_world(time.time())
        r = self.handoff("remind", "--dry-run")
        self.assertEqual(r.stdout.splitlines(), ["remind: not silent", "Waiting on you:"] + [
            f"- {x}" for x in (self.PARKED, self.LIMIT, self.WAITS, self.LOGIN, self.COLOURS, self.RECEIPTS)])
        self.assertEqual(self.handoff("remind", "--dry-run").stdout, r.stdout, "a dry run keeps no state: the same again")
        self.assertFalse((self.tmp / "claude" / "flowkit" / "remind.json").exists())
        self.assertFalse(self.calls.exists(), "nothing typed, rung or started")
        self.assertFalse((self.tmp / "claude" / "flowkit" / "remind").exists(), "no reminder written")

    def test_remind_is_silent_when_off_away_or_at_the_computer(self):
        self.env["XDG_CONFIG_HOME"] = str(self.tmp)
        first = lambda: self.handoff("remind", "--dry-run").stdout.splitlines()
        self.idle(3600)
        self.assertEqual(first(), ["remind: silent, off (remind=on in handoff.conf turns it on)", "nothing to say"])
        self.handoff("conf", "remind", "on")
        self.assertEqual(first()[0], "remind: not silent")
        self.idle(599)
        self.assertEqual(first()[0], "remind: silent, at the computer (last input 599 s ago)")
        self.idle(600)
        self.assertEqual(first()[0], "remind: not silent", "ten minutes without a key or the mouse: not at the computer")
        self.flag.write_text("2026-11-18 23:10\n")
        self.idle(5)
        self.assertEqual(first()[0], "remind: silent, away since 2026-11-18 23:10; at the computer (last input 5 s ago)")
        self.flag.unlink()
        # not a Mac: the newest tmux client activity stands in, and no answer at all is not "at the computer"
        self.idle(None)
        self.assertEqual(first()[0], "remind: not silent")
        (self.tmp / "bin" / "tmux").write_text(
            f"#!/bin/sh\n[ \"$1\" = list-clients ] && printf '%s\\n' {int(time.time()) - 4000} {int(time.time()) - 30}\n")
        self.assertRegex(first()[0], r"^remind: silent, at the computer \(last input 3\d s ago\)$")

    def test_remind_says_at_once_once_and_the_digest_only_when_the_set_changed(self):
        T, H = time.time(), 3600
        self.remind_world(T)
        second = self.PARKED.replace("pane 2", "pane 3")
        with mock.patch.dict(os.environ, self.env, clear=True):
            loader = importlib.machinery.SourceFileLoader("flowkit_handoff_remind", str(HANDOFF))
            handoff = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
            loader.exec_module(handoff)

            def look(now, panes=("%4", "%5"), mark=True):
                if panes is not None:
                    self.park(now, *panes)  # the tick writes it again at every run
                found, stale = handoff.remind_found(now)
                heading, lines, state = handoff.remind_pass(found, stale, handoff.remind_state(), now)
                if mark:
                    handoff.remind_mark(state)
                return (heading, lines) if lines else None

            everything = [self.PARKED, self.LIMIT, self.WAITS, self.LOGIN, self.COLOURS, self.RECEIPTS]
            self.assertEqual(look(T, ["%4"]), ("Waiting on you", everything), "the first look is a digest")
            self.assertIsNone(look(T + 60, ["%4"]), "said once")
            self.assertEqual(look(T + 120), ("Needs you now", [second]), "a second pane parks: that one alone, at once")
            self.assertIsNone(look(T + 180))
            # what was found but not kept as said (a silent pass, a push that did not arrive) is still to say
            self.limit_record("s-zz", name="demo-s7", at=200)
            other = "demo-s7 stopped on the usage limit and flowkit cannot continue it"
            self.assertEqual(look(T + 200, mark=False), ("Needs you now", [other]))
            self.assertEqual(look(T + 210), ("Needs you now", [other]))
            self.assertIsNone(look(T + 220))
            # a session that waits and an item with another gate never go at once: they wait for the digest
            self.agents([{"name": "demo-s5", "sessionId": "s5", "status": "waiting", "waitingFor": "permission prompt"},
                         {"name": "demo-s8", "sessionId": "s8", "status": "waiting"}])
            self.assertIsNone(look(T + 300))
            # two hours after the last digest: all of it, since the set is not the one that digest saw
            grown = [self.PARKED, second, self.LIMIT, other, self.WAITS, "demo-s8 waits on a prompt",
                     self.LOGIN, self.COLOURS, self.RECEIPTS]
            self.assertIsNone(look(T + 2 * H - 1))
            self.assertEqual(look(T + 2 * H), ("Waiting on you", grown))
            self.assertIsNone(look(T + 4 * H + 1), "the same set two hours on: no digest")
            (self.stories / "side.tests.md").unlink()
            self.assertIsNone(look(T + 4 * H + 60), "a change between two digests waits for the next")
            self.assertEqual(look(T + 6 * H + 2), ("Waiting on you", grown[:-1]))
            # an item that leaves ready and turns ready again is said again, at once
            queue = self.stories / "demo.tests.md"
            ready = queue.read_text()
            queue.write_text(ready.replace("Login · !1 · iPhone 15 · ready to test", "Login · !1 · iPhone 15 · tested"))
            self.assertIsNone(look(T + 6 * H + 60))
            queue.write_text(ready)
            self.assertEqual(look(T + 6 * H + 120), ("Needs you now", [self.LOGIN]))
            # the tick stops (no tmux client): parked.json goes stale and says nothing, and its panes are
            # not said again when it runs again
            self.assertIsNone(look(T + 6 * H + 120 + 301, panes=None))
            self.assertNotIn("parked:%4:1002", {key for key, _, _ in handoff.remind_found(T + 6 * H + 500)[0]})
            self.assertIsNone(look(T + 6 * H + 600))
            # nothing waits: no digest, and what waits again later is a change
            queue.unlink()
            self.agents([])
            shutil.rmtree(self.tmp / "claude" / "flowkit" / "retry")
            self.assertIsNone(look(T + 8 * H + 10, []))
            self.agents([{"name": "demo-s5", "sessionId": "s5", "status": "waiting", "waitingFor": "permission prompt"}])
            self.assertIsNone(look(T + 9 * H, []))
            self.assertEqual(look(T + 10 * H + 20, []), ("Waiting on you", [self.WAITS]))
            state = json.loads((self.tmp / "claude" / "flowkit" / "remind.json").read_text())
            self.assertEqual(state, {"once": {}, "digest": {"at": T + 10 * H + 20, "keys": ["waiting:s5:permission prompt"]}})
            push = handoff.remind_push("Waiting on you", ["a pane is parked " + "x" * 300, "one more"])
            self.assertEqual(len(push), 195, "the heading's colon and space count too")
            self.assertTrue(push.endswith("x… (+1 more in remind-latest.md)"))

    def push_answer(self, sid, answer, at):
        """What a PushNotification call made at epoch `at` and its answer leave in a transcript."""
        when = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat().replace("+00:00", "Z")
        return (json.dumps({"type": "assistant", "timestamp": when(at), "message": {"content": [
                    {"type": "tool_use", "id": f"tu-{sid}", "name": "PushNotification", "input": {"message": "x"}}]}})
                + "\n" + json.dumps({"type": "user", "timestamp": when(at + 1), "message": {"content": [
                    {"type": "tool_result", "tool_use_id": f"tu-{sid}", "content": answer}]}}) + "\n")

    def test_remind_rings_the_oldest_idle_reporter_until_one_pushes(self):
        T = time.time()
        self.remind_world(T)
        (self.stories / "done.md").write_text("# done\n\n## done-s1 · 2026-11-19 09:00\nx\n\n"
                                              "## Final report · 2026-11-19 10:00\nall done\n")
        reporters = [  # name, kind, status, pid, pane, started
            ("demo-coord", "interactive", "idle", 4000, "%6", 10), ("done-report", "interactive", "idle", 4002, "%12", 50),
            ("busy-report", "interactive", "busy", 4007, "%13", 60), ("bg-report", "background", "idle", 4008, None, 70),
            ("demo-report", "interactive", "idle", 4001, "%7", 100), ("draft-report", "interactive", "idle", 4005, "%9", 150),
            ("quiet-report", "interactive", "idle", 4006, "%10", 170), ("side-report", "interactive", "idle", 4003, "%8", 200),
            ("late-report", "interactive", "idle", 4004, "%11", 300)]
        self.agents([{"name": n, "kind": k, "status": s, "pid": pid, "sessionId": f"sid-{n}", "startedAt": t}
                     for n, k, s, pid, _, t in reporters]
                    + [{"name": "demo-s5", "sessionId": "s5", "status": "waiting", "waitingFor": "permission prompt"}])
        projects = self.tmp / "claude" / "projects" / "-repo"
        projects.mkdir(parents=True)
        # a push from before the ring never counts
        (projects / "sid-demo-report.jsonl").write_text(self.push_answer("old", "Terminal notification sent.", T - 3600))
        # what a reporter's push leaves in its transcript once the ring is typed into its pane
        for pane, sid, answer in (("%10", "sid-quiet-report", "Not sent — this terminal is active, so your output "
                                   "here already reaches the user"),
                                  ("%8", "sid-side-report", "Terminal notification sent. Mobile push requested.")):
            (self.tmp / f"push{pane}").write_text(self.push_answer(sid, answer, T + 60))
            (self.tmp / f"push{pane}.to").write_text(str(projects / f"{sid}.jsonl"))
        stub = self.tmp / "bin" / "tmux"
        stub.write_text("#!/bin/sh\ncase \"$1\" in\n"
                        "list-panes) printf '%s\\n' " + " ".join(f"'{pid} {pane} 0'" for *_, pid, pane, _ in reporters if pane)
                        + ";;\ncapture-pane) if [ \"$4\" = %9 ]; then echo '│ > half typed'; else echo '│ > '; fi;;\n"
                        f"send-keys) echo tmux \"$@\" >> {self.calls}; f={self.tmp}/push$3; "
                        "if [ -f \"$f\" ]; then cat \"$f\" >> \"$(cat \"$f.to\")\"; fi;;\n"
                        f"*) echo tmux \"$@\" >> {self.calls};; esac\n")
        stub.chmod(0o755)
        with mock.patch.dict(os.environ, self.env, clear=True):
            loader = importlib.machinery.SourceFileLoader("flowkit_handoff_ring", str(HANDOFF))
            handoff = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
            loader.exec_module(handoff)
            handoff.REMIND_WAIT = 0.3
            with contextlib.redirect_stdout(io.StringIO()) as out:
                handoff.remind()
            lines = out.getvalue().splitlines()
            self.assertEqual(lines[:4], ["ring demo-report: no push in 0.3 s", "ring draft-report: draft",
                                         "ring quiet-report: Not sent — this terminal is active, so your output here "
                                         "already reaches the user",
                                         "ring side-report: Terminal notification sent. Mobile push requested."])
            self.assertRegex(lines[4], r"^remind: sent through side-report \(.*/flowkit/remind/\d{8}-\d{6}\.md\)$")
            self.assertEqual(len(lines), 5)
            typed = self.calls.read_text().splitlines()
            self.assertEqual([t.split(" -l ")[0] for t in typed],
                             ["tmux send-keys -t %7", "tmux send-keys -t %10", "tmux send-keys -t %8"])
            push = ("Waiting on you: a pane is parked at a start-up prompt: Accessing workspace: (tab demo, pane 2, "
                    "story demo) (+5 more in remind-latest.md)")
            self.assertTrue(typed[0].split(" -l ")[1].startswith("reminder: from flowkit's tick, not the user. "))
            self.assertTrue(typed[0].endswith(f"Message: {push} ; run-shell sleep 0.5 ; send-keys -t %7 Enter"))
            files = list((self.tmp / "claude" / "flowkit" / "remind").glob("*.md"))
            self.assertEqual(len(files), 1)
            text = files[0].read_text()
            self.assertTrue(text.startswith("# Waiting on you · "))
            self.assertEqual(text.split("\n\n", 1)[1], "".join(f"- {x}\n" for x in (
                self.PARKED, self.LIMIT, self.WAITS, self.LOGIN, self.COLOURS, self.RECEIPTS)))
            self.assertEqual((self.tmp / "claude" / "flowkit" / "remind-latest.md").read_text(), text)
            self.assertTrue((self.tmp / "claude" / "flowkit" / "remind.json").exists(), "kept as said once a push arrived")
            with contextlib.redirect_stdout(io.StringIO()) as out:
                handoff.remind()
            self.assertEqual(out.getvalue(), "remind: nothing to say\n", "said: nobody is rung again")
            self.assertEqual(len(self.calls.read_text().splitlines()), 3)
            # no tmux on this machine: every reporter is a miss, and the push falls through to notify
            (self.tmp / "claude" / "flowkit" / "remind.json").unlink()
            conf = {"remind": "on", "notify": "true"}
            with mock.patch.object(handoff, "pane_of", side_effect=FileNotFoundError("tmux")), \
                    mock.patch.object(handoff, "setting", side_effect=lambda key, default: conf.get(key, default)), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                handoff.remind()
            lines = out.getvalue().splitlines()
            self.assertEqual(lines[:-2], [f"ring {n}-report: tmux or ps did not answer (FileNotFoundError)"
                                          for n in ("demo", "draft", "quiet", "side", "late")])
            self.assertEqual(lines[-2], "notify: sent")
            self.assertTrue(lines[-1].startswith("remind: sent through notify ("))

    def test_remind_falls_back_to_notify_then_the_mac_and_keeps_only_what_arrived(self):
        self.remind_world(time.time())
        said = self.tmp / "claude" / "flowkit" / "remind.json"
        notified, mac = self.tmp / "notified", self.tmp / "mac"
        self.handoff("conf", "notify", f"cat > {notified}")
        osascript = self.tmp / "bin" / "osascript"
        osascript.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {mac}\nexit 0\n")
        osascript.chmod(0o755)
        out = self.handoff("remind").stdout.splitlines()
        self.assertEqual(out[0], "notify: sent")
        self.assertRegex(out[1], r"^remind: sent through notify \(")
        self.assertTrue(notified.read_text().startswith("Waiting on you: a pane is parked at a start-up prompt"))
        self.assertTrue(said.exists())
        self.assertFalse(mac.exists())
        said.unlink()
        self.handoff("conf", "notify", "false")
        out = self.handoff("remind").stdout.splitlines()
        self.assertEqual(out[0], "notify: failed")
        self.assertRegex(out[1], r"^remind: sent through macOS notification \(")
        self.assertEqual(mac.read_text().splitlines()[-1], notified.read_text(), "the push is osascript's last argument")
        said.unlink()
        osascript.write_text("#!/bin/sh\nexit 1\n")
        out = self.handoff("remind").stdout.splitlines()
        self.assertRegex(out[-1], r"^remind: not delivered, next try \d\d:\d\d \(")
        self.assertEqual(list(json.loads(said.read_text())), ["failed"], "nothing arrived: nothing kept as said")
        said.unlink()
        self.assertEqual(len(list((self.tmp / "claude" / "flowkit" / "remind").glob("*.md"))), 1,
                         "the same text again: no second file")
        with (self.tmp / "claude" / "flowkit" / "remind.lock").open("w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual(self.handoff("remind").stdout, "remind: another pass is running\n")
        self.idle(5)
        self.assertRegex(self.handoff("remind").stdout, r"^remind: silent, at the computer")
        self.assertFalse(said.exists())

    def test_remind_waits_ten_minutes_before_it_tries_an_undelivered_reminder_again(self):
        self.remind_world(time.time())
        self.agents([{"name": "demo-report", "kind": "interactive", "status": "idle", "pid": 4001, "sessionId": "sid-r",
                      "startedAt": 1}])
        stub = self.tmp / "bin" / "tmux"
        stub.write_text("#!/bin/sh\ncase \"$1\" in\nlist-panes) echo '4001 %7 0';;\ncapture-pane) echo '│ > ';;\n"
                        f"*) echo tmux \"$@\" >> {self.calls};; esac\n")
        stub.chmod(0o755)
        osascript = self.tmp / "bin" / "osascript"
        osascript.write_text("#!/bin/sh\nexit 1\n")
        osascript.chmod(0o755)
        said = self.tmp / "claude" / "flowkit" / "remind.json"
        with mock.patch.dict(os.environ, self.env, clear=True):
            loader = importlib.machinery.SourceFileLoader("flowkit_handoff_retry", str(HANDOFF))
            handoff = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
            loader.exec_module(handoff)
            handoff.REMIND_WAIT = 0.2

            def one_pass():
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    handoff.remind()
                return out.getvalue().splitlines()

            rung = lambda: self.calls.read_text().count(" -l reminder: ")
            lines = one_pass()
            self.assertEqual(lines[0], "ring demo-report: no push in 0.2 s")
            self.assertRegex(lines[1], r"^remind: not delivered, next try \d\d:\d\d \(")
            lines = one_pass()
            self.assertEqual(len(lines), 1)
            self.assertRegex(lines[0], r"^remind: not delivered at \d\d:\d\d, next try \d\d:\d\d$")
            self.assertEqual(rung(), 1, "a minute on: the reporter is not rung again")
            self.limit_record("s-zz", name="demo-s7", at=200)
            self.assertEqual(one_pass()[0], "ring demo-report: no push in 0.2 s", "something else waits: tried at once")
            self.assertEqual((rung(), len(one_pass()), rung()), (2, 1, 2))
            state = json.loads(said.read_text())
            state["failed"]["at"] -= 601
            said.write_text(json.dumps(state))
            osascript.write_text("#!/bin/sh\nexit 0\n")
            self.assertRegex(one_pass()[-1], r"^remind: sent through macOS notification \(")
            self.assertEqual(rung(), 3, "ten minutes on, the same reminder is tried again")
            self.assertNotIn("failed", json.loads(said.read_text()), "delivered: nothing left to try")


if __name__ == "__main__":
    unittest.main()
