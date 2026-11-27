"""Run with `python3 -m unittest discover plugins/handoff/tests`. `flowkit-driver test`, run as a
driver's author runs it: against a stub driver that keeps each place as a process behind a named
pipe, with every request answered, with a required one failing and with the optional ones left
out, with a screen that loses the prompt mark; against tmux in a server of its own; and against
the screen driver where screen is installed.
Nothing reaches the user's tmux, screen sessions or files."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
TEST = PLUGIN / "bin" / "flowkit-driver"
STUB = r"""#!/bin/bash
# a place is a process that reads a named pipe and writes a file
d=%s
grep -qx "$1" $d/fails 2>/dev/null && { echo "the stub fails at $1" >&2; exit 1; }
grep -qx "$1" $d/silent 2>/dev/null && exit 2
case "$1" in
open) id="place-$(ls $d/places | wc -l | tr -d ' ')"
      mkfifo "$d/places/$id.in"
      ( cd "$4" && exec 3<>"$d/places/$id.in" && exec sh -c "$5" <&3 >"$d/places/$id.out" 2>&1 ) &
      echo $! > "$d/places/$id.pid"
      echo "$id";;
list) for f in $d/places/*.pid; do
        [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null && printf '%%s\t%%s\n' "$(basename "$f" .pid)" "$(cat "$f")"
      done; exit 0;;
type) printf '%%s' "$3" > "$d/places/$2.in";;
screen) cat "$d/places/$2.out";;
label) echo "$3" > "$d/places/$2.label";;
close) pkill -P "$(cat "$d/places/$2.pid")"; kill "$(cat "$d/places/$2.pid")" 2>/dev/null; rm -f "$d/places/$2.pid";;
*) exit 2;;
esac
"""


class TestCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = d = Path(tempfile.mkdtemp(dir="/tmp" if os.path.isdir("/tmp") else None))  # a short path: sockets live under it
        self.addCleanup(shutil.rmtree, d, True)
        for sub in ("bin", "places", "home", "claude", "tmp"):
            (d / sub).mkdir()
        (d / "bin" / "flowkit-driver-stub").write_text(STUB % d)
        (d / "bin" / "flowkit-driver-stub").chmod(0o755)
        self.env = {"PATH": f"{d / 'bin'}:{os.environ['PATH']}", "HOME": str(d / "home"), "XDG_CONFIG_HOME": str(d / "home"),
                    "CLAUDE_CONFIG_DIR": str(d / "claude"), "TMPDIR": str(d / "tmp"), "TMUX_TMPDIR": str(d),
                    "SCREENDIR": str(d / "scr")}

    def run_test(self, *args, **env):
        out = subprocess.run([str(TEST), "test", *args], capture_output=True, text=True, env={**self.env, **env}, timeout=120)
        return out.returncode, out.stdout + out.stderr

    def verdicts(self, said):
        return dict(re.findall(r"^(open|list|type|screen|label|pick|close) +(passed|failed|not answered|not asked)", said, re.M))

    def test_a_driver_that_answers_every_request_passes(self):
        code, said = self.run_test("stub")
        self.assertEqual((code, self.verdicts(said)), (0, {"open": "passed", "list": "passed", "type": "passed", "screen": "passed",
                                                           "label": "passed", "pick": "not asked", "close": "passed"}), said)
        self.assertTrue(said.endswith("stub: passed\n"), said)
        self.assertEqual([f.name for f in (self.tmp / "places").glob("*.pid")], [], "its place is closed")
        self.assertEqual((self.tmp / "places" / "place-0.label").read_text(), "waiting\n")
        self.assertNotIn("flowkit-driver-test", (self.tmp / "claude" / "flowkit" / "sessions.json").read_text())

    def test_a_required_request_that_fails_ends_non_zero(self):
        (self.tmp / "fails").write_text("type\n")
        code, said = self.run_test("stub")
        self.assertEqual((code, self.verdicts(said)["type"], self.verdicts(said)["open"]), (1, "failed", "passed"), said)
        self.assertIn("type   failed · not typed\n", said)
        self.assertTrue(said.endswith("stub: failed\n"), said)
        (self.tmp / "fails").write_text("open\n")
        code, said = self.run_test("stub")
        self.assertEqual(code, 1, said)
        self.assertIn("open   failed · the stub fails at open\nlist   not asked · open gave no place\n", said)

    def test_what_a_driver_leaves_out_is_named_with_what_goes_without_it(self):
        (self.tmp / "silent").write_text("screen\nlabel\nclose\n")
        code, said = self.run_test("stub")
        self.assertEqual((code, self.verdicts(said)), (0, {"open": "passed", "list": "passed", "type": "passed",
                                                           "screen": "not answered", "label": "not answered",
                                                           "pick": "not asked", "close": "not answered"}), said)
        self.assertIn("screen not answered · without it a launch's line has no Remote Control link", said)
        self.assertIn("close  not answered · without it a finished session's place stays until you close it", said)
        (self.tmp / "silent").write_text("list\n")
        code, said = self.run_test("stub")
        self.assertEqual(code, 1, said)
        self.assertIn("list   not answered · required: no session is found where it runs\n", said)

    def test_a_screen_that_loses_the_prompt_mark_fails(self):
        stub = self.tmp / "bin" / "flowkit-driver-stub"
        stub.write_text(stub.read_text().replace('screen) cat "$d/places/$2.out";;', 'screen) LC_ALL=C tr -d "\\342\\235\\257" < "$d/places/$2.out";;'))
        code, said = self.run_test("stub")
        self.assertEqual((code, self.verdicts(said)["screen"]), (1, "failed"), said)

    def test_a_name_with_no_driver_and_the_background_are_not_tried(self):
        self.assertEqual(self.run_test("nope"), (2, "no launch driver 'nope': no executable flowkit-driver-nope on the PATH\n"))
        self.assertEqual(self.run_test("background")[0], 2)
        code, said = self.run_test("tmux", TMUX="", TMUX_PANE="")
        self.assertEqual((code, said), (2, "tmux: not inside tmux: run this inside it, or name a server with FLOWKIT_TMUX_SOCKET\n"))

    @unittest.skipUnless(shutil.which("tmux"), "tmux is not installed")
    def test_tmux_passes_in_a_server_of_its_own(self):
        sock = f"fd-{os.getpid()}"
        real = lambda *a: subprocess.run(["tmux", "-L", sock, *a], capture_output=True, text=True, env=self.env)
        self.addCleanup(real, "kill-server")
        # never the user's shell rc; `exec`, so the pane's own process is the shell a launch is typed into (a `sh`
        # that keeps running above it is not what a session is looked for under)
        shell = "exec /bin/bash --noprofile --norc"
        real("-f", "/dev/null", "new-session", "-d", "-s", "main", "-x", "200", "-y", "50", shell)
        real("set-option", "-g", "default-shell", "/bin/sh")
        real("set-option", "-g", "default-command", shell)
        code, said = self.run_test("tmux", FLOWKIT_TMUX_SOCKET=sock)
        self.assertEqual((code, self.verdicts(said)), (0, {"open": "passed", "list": "passed", "type": "passed", "screen": "passed",
                                                           "label": "passed", "pick": "not asked", "close": "passed"}), said)
        self.assertEqual(real("list-windows", "-a", "-F", "#{window_name}").stdout, "bash\n", "its tab is gone")

    @unittest.skipUnless(shutil.which("screen"), "screen is not installed")
    def test_the_screen_driver_passes(self):
        (self.tmp / "scr").mkdir(mode=0o700)
        code, said = self.run_test("screen")
        self.assertEqual((code, self.verdicts(said)), (0, {"open": "passed", "list": "passed", "type": "passed",
                                                           "screen": "not answered", "label": "passed", "pick": "not asked",
                                                           "close": "passed"}), said)
        listed = subprocess.run(["screen", "-ls"], capture_output=True, text=True, env=self.env).stdout
        self.assertNotIn("flowkit.", listed, "its screen session is gone")


if __name__ == "__main__":
    unittest.main()
