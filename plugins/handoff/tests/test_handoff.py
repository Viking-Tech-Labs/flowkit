"""Run with `python3 -m unittest discover plugins/handoff/tests`. Each test gets its own config
dir and temp dir, a stub `claude` that serves a session list and a usage answer and records what
else it was called with, and a stub `tmux` that serves a pane list, so nothing reaches a real
session or the user's files."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

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
        self.agents([])
        self.panes("")

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

    def gate(self):
        out = self.handoff("question-gate", stdin=json.dumps({"tool_name": "AskUserQuestion"})).stdout
        return json.loads(out)["hookSpecificOutput"] if out else None

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
                      "the last slice takes it out of Draft, watches the pipeline to green, no merge)", state())
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

    def spawn_in_tmux(self, exits):
        """A throwaway tmux server whose one pane runs bash with a fake `claude` first on its PATH:
        the first `exits` launches exit 1 at once, the next writes its pid and sleeps; `claude
        agents --json` lists that pid once the file is there. Returns the pane id and the run."""
        repo = self.spawn_repo()
        (self.tmp / "flowkit" / "handoff.conf").write_text("driver=tmux\nreporter=off\n")
        (self.tmp / "bin" / "tmux").unlink()
        pidfile, launches = self.tmp / "claude.pid", self.tmp / "launches"
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
            f"echo $$ > {pidfile}\n"
            "exec sleep 30\n")
        fake.chmod(0o755)
        sock = f"flowkit-test-{os.getpid()}"
        real = lambda *a: subprocess.run(["tmux", "-L", sock, *a], capture_output=True, text=True)
        self.addCleanup(real, "kill-server")
        real("-f", "/dev/null", "new-session", "-d", "-x", "200", "-y", "50", "/bin/bash --noprofile --norc")
        pane = real("list-panes", "-F", "#{pane_id}").stdout.split()[0]
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

    def test_away_writes_the_flag_then_nudges_and_back_lists(self):
        self.agents([
            {"name": "s3", "kind": "interactive", "pid": 1, "status": "waiting", "waitingFor": "permission prompt"},
        ])
        self.usage_answer()
        out = self.handoff("away").stdout.splitlines()
        self.assertTrue(self.flag.is_file())
        self.assertTrue(out[0].startswith("away: since "))
        self.assertTrue(out[1].startswith("Week 49% used, resets "))
        self.assertEqual(out[2], "permission s3: waits on a permission prompt; answer it from the phone")
        out = self.handoff("back").stdout.splitlines()
        self.assertFalse(self.flag.exists())
        self.assertEqual(out, ["away: off", "s3: waits on permission prompt"])
        self.assertFalse(self.calls.exists())

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
        for kw, error in ((dict(), "rate_limit"), (dict(), "authentication_failed"), (dict(), "invalid_request"),
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


if __name__ == "__main__":
    unittest.main()
