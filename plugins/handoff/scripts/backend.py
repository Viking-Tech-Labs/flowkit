"""Where a session runs and how flowkit reaches it there.

A session runs in a tmux pane (driver=tmux) or as one of Claude Code's own background sessions
(driver=background, `claude --bg`). Whatever finds a session, reads what it shows or types into it
asks one of the two backends here, TMUX and BACKGROUND, which answer the same requests:

  available()                 its program is installed
  locate(pid=, sid=, name=)   the place a session runs at, None when this backend has none for it:
                              TMUX by the session's process, BACKGROUND by its session id or its
                              name. A place is (id, copy, row): what the backend calls it (a pane
                              id, a session id), whether it sits in copy mode, where typed keys
                              reach no session, and the `claude agents` row it was found through
  here()                      the place of the session this process runs in
  screen(place)               the rows it shows, each as (text, typed): the row as plain text, and
                              the same row with what is only suggested blanked
  bottom(place)               its last rows as plain text
  parked(place)               why its prompt box takes no line, "" when it does
  draft(place)                text sits in its prompt box
  type(place, text, how)      one line as the session's next prompt: "" once typed, else why not.
                              `how` names the variant, see LINE
  escape(place)               Escape alone: it closes a dialog a typed command left open
  story(place)                the story the session was started for, "" when none is known
  last_input()                when the user last used a terminal of this backend, as a time

A request a backend has no answer to gets the empty one (no screen, no story, nothing parked), so
a caller asks without knowing which it holds. A program that is missing or hangs raises OSError
or subprocess.SubprocessError: the caller says what that means where it is. A place may also be a
bare pane id for TMUX, known some other way than through locate.

The same for both, read from Claude Code's own list: agents() and mode(row), which says busy,
waiting or idle.

This file imports nothing of the plugin, so a command copied out of it loads it by path.
"""
import collections
import json
import os
import re
import shutil
import subprocess
import time

Place = collections.namedtuple("Place", "id copy row")

# how a line is typed
LINE = "line"        # the text, then Enter
OVER = "over"        # what is half typed is cleared first
FREE = "free"        # only where nothing is in its way: a prompt box on screen, empty, no dialog below it, not in
#                      copy mode; a background session that does not wait on a prompt
ESCAPE = "escape"    # Escape first, which ends the turn of a session that waits on a question, then as OVER; a
#                      background session is stopped for it even while it waits
COMMAND = "command"  # a slash command, as FREE: what it leaves open is closed with escape(); a background
#                      session has no prompt box to take one

PAUSE = 0.5  # seconds between the text and Enter: the TUI reads an Enter that comes with the text as part of a paste
BOTTOM = 8
STOPPING = 10  # seconds a stopped background session's process may take to go
GRACE = 3  # seconds from the process going to the resume after ESCAPE: measured on Claude Code 2.1.302, a resume
#            within a second of the stop starts a copy under a new id, one after 2 s continues

PROMPT_LINE = re.compile(r"^[│|\s]*[>❯]\s?(.*?)[\s│|]*$")
# what `capture-pane -e` adds to a row: a change of attributes (its codes are group 1), a link, any other sequence
SEQUENCE = re.compile(r"\x1b\[([\d;:]*)m|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[^@-~]*[@-~]")
COLOUR = re.compile(r"\b[345]8;(?:5;\d*|2;\d*;\d*;\d*)")  # a colour's own numbers are no attributes
DIALOG = re.compile(r"enter to (?:confirm|select|continue)|esc to (?!interrupt)\w+|^\s*(?:[❯>]\s*)?\d+\.\s", re.I | re.M)


def tmux(*args):
    sock = os.environ.get("FLOWKIT_TMUX_SOCKET")
    return subprocess.run(
        ["tmux", *(["-L", sock] if sock else []), *args], capture_output=True, text=True, timeout=10
    )


def agents(*args):
    """`claude agents --json`: the sessions Claude Code lists, with `--all` the stopped ones too."""
    return json.loads(
        subprocess.run(["claude", "agents", "--json", *args], capture_output=True, text=True, timeout=15).stdout
    )


def mode(row):
    """busy, waiting or idle. A background entry's `status` is there while its process runs and
    wins over `state`, which can still say working after the turn ended."""
    status = row.get("status")
    if status is None:
        status = {"working": "busy", "blocked": "waiting"}.get(row.get("state"), "idle")
    return status


def parents():
    """{pid: parent pid} of every process."""
    ps = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=10).stdout
    return {int(a): int(b) for a, b in (row.split() for row in ps.splitlines() if len(row.split()) == 2)}


def rows(capture):
    """A captured screen as rows of (text, typed). Claude Code draws a prompt suggestion dim in an
    empty prompt box, which a plain capture shows as typed text, so `typed` has the dim characters
    blanked. tmux writes a change of attributes where it happens and not again on the next row, so
    the capture is read as one stream."""
    found, dim = [], False
    for line in capture.splitlines():
        text = typed = ""
        parts = SEQUENCE.split(line)  # text, an attribute change's codes (None for another sequence), text, …
        for part, codes in zip(parts[::2], [*parts[1::2], None]):
            text, typed = text + part, typed + (" " * len(part) if dim else part)
            for code in COLOUR.sub("c", codes).split(";") if codes is not None else ():
                dim = code == "2" if code in ("", "0", "2", "22") else dim
        found.append((text, typed))
    return found


def drafted(row):
    """The text typed into the prompt box a row holds: what follows its prompt mark and is not dim."""
    m = PROMPT_LINE.match(row[0])
    return row[1][m.start(1):m.end(1)].strip()


class Backend:
    """The requests, each with the answer of a backend that has none."""
    name = program = ""

    def available(self):
        return bool(shutil.which(self.program))

    def locate(self, pid=None, sid=None, name=None):
        return None

    def here(self):
        return None

    def screen(self, place):
        return []

    def bottom(self, place):
        return []

    def parked(self, place):
        return ""

    def draft(self, place):
        return False

    def type(self, place, text, how=LINE, settled=None):
        return "not typed"

    def escape(self, place):
        return False

    def story(self, place):
        return ""

    def last_input(self):
        return None


class Tmux(Backend):
    """An interactive session in a tmux pane; FLOWKIT_TMUX_SOCKET names another server than the
    default one."""
    name = program = "tmux"

    @staticmethod
    def pane(place):
        return place.id if isinstance(place, Place) else place

    def finder(self):
        """One reading of the panes and of the processes, for any number of sessions: a function
        from a session's pid to its place. A pane's pid is the shell that started the session, so
        the session is found through its parent processes."""
        panes = {}
        for line in tmux("list-panes", "-a", "-F", "#{pane_pid} #{pane_id} #{pane_in_mode}").stdout.splitlines():
            p, pane, in_mode = (line.split() + ["", "", ""])[:3]
            if p.isdigit():
                panes[int(p)] = (pane, in_mode)
        above = parents()

        def find(pid):
            for _ in range(4):
                if pid in panes:
                    pane, in_mode = panes[pid]
                    return Place(pane, in_mode != "0", None)
                pid = above.get(pid)
                if pid is None:
                    return None
            return None
        return find

    def locate(self, pid=None, sid=None, name=None):
        return self.finder()(pid)

    def here(self):
        pane = os.environ.get("TMUX_PANE")
        return Place(pane, False, None) if pane else None

    def screen(self, place):
        return rows(tmux("capture-pane", "-p", "-e", "-t", self.pane(place)).stdout)

    def bottom(self, place):
        found = tmux("capture-pane", "-p", "-t", self.pane(place)).stdout.splitlines()
        while found and not found[-1].strip():
            found.pop()
        return found[-BOTTOM:]

    def parked(self, place):
        """no prompt box (none on screen), dialog (rows of one sit below it: a menu's numbered
        options, its Enter or Esc hint) or draft. Only rows below the last prompt-box line count,
        so a dialog quoted in the conversation above it never does."""
        found = self.screen(place)
        last = max((i for i, row in enumerate(found) if PROMPT_LINE.match(row[0])), default=None)
        if last is None:
            return "no prompt box"
        if DIALOG.search("\n".join(row[0] for row in found[last + 1:])):
            return "dialog"
        return "draft" if drafted(found[last]) else ""

    def draft(self, place):
        """Text sits in the prompt box: the last row that starts with the prompt mark."""
        found = [row for row in self.screen(place) if PROMPT_LINE.match(row[0])]
        return bool(found) and bool(drafted(found[-1]))

    def type(self, place, text, how=LINE, settled=None):
        """The text and Enter go in one tmux call, so nothing else typing into panes lands in
        between. Why not: copy mode and parked's (FREE, COMMAND), not settled (ESCAPE, when
        `settled` is given and, called after the Escape, does not answer True), not typed (tmux
        refused the keys)."""
        pane = self.pane(place)
        if how in (FREE, COMMAND):
            why = "copy mode" if isinstance(place, Place) and place.copy else self.parked(place)
            if why:
                return why
        if how == ESCAPE:
            tmux("send-keys", "-t", pane, "Escape")
            if settled is not None and not settled():
                return "not settled"
            time.sleep(PAUSE)
        clear = ("send-keys", "-t", pane, "C-u", ";") if how in (OVER, ESCAPE) else ()
        sent = tmux(*clear, "send-keys", "-t", pane, "-l", text, ";", "run-shell", f"sleep {PAUSE}",
                    ";", "send-keys", "-t", pane, "Enter")
        return "" if sent.returncode == 0 else "not typed"

    def escape(self, place):
        return tmux("send-keys", "-t", self.pane(place), "Escape").returncode == 0

    def story(self, place):
        return tmux("show-options", "-wqv", "-t", self.pane(place), "@flowkit_story").stdout.strip()

    def last_input(self):
        seen = [int(x) for x in tmux("list-clients", "-F", "#{client_activity}").stdout.split() if x.isdigit()]
        return max(seen) if seen else None


class Background(Backend):
    """A `claude --bg` session. It has no prompt box and no screen: a line reaches it as the
    prompt of a resume under its own id."""
    name, program = "background", "claude"

    def locate(self, pid=None, sid=None, name=None):
        """The newest background entry with that session id, or without one that name, among the
        stopped ones too: a stopped session keeps its entry, without a pid."""
        if not (sid or name):
            return None
        found = [a for a in agents("--all") if isinstance(a, dict) and a.get("kind") == "background"
                 and (a.get("sessionId") == sid if sid else a.get("name") == name)]
        row = max(found, key=lambda a: a.get("startedAt") or 0) if found else None
        return row and Place(row.get("sessionId"), False, row)

    def type(self, place, text, how=LINE, settled=None):
        """`claude --bg --resume` hands a stopped session the prompt under its own id; a live one
        would get a copy instead, so a live one is stopped first. Why not: waiting (FREE: a stop
        would throw its question away), no prompt box (COMMAND), not stopped (its process did not
        go), not typed (claude refused the resume)."""
        row = place.row
        if how == COMMAND:
            return "no prompt box"
        if how == FREE and mode(row) == "waiting":
            return "waiting"
        if row.get("pid"):
            subprocess.run(["claude", "stop", row["id"]], capture_output=True, text=True, timeout=30)
            for _ in range(int(STOPPING / PAUSE)):  # `stop` returns before the process is gone
                time.sleep(PAUSE)
                now = self.locate(sid=row.get("sessionId"), name=row.get("name"))
                if now is None or not now.row.get("pid"):
                    break
            else:
                return "not stopped"
            if how == ESCAPE:
                time.sleep(GRACE)
        sent = subprocess.run(["claude", "--bg", "--resume", row["sessionId"], text], cwd=row.get("cwd") or None,
                              capture_output=True, text=True, timeout=60)
        return "" if sent.returncode == 0 else "not typed"


TMUX, BACKGROUND = Tmux(), Background()
