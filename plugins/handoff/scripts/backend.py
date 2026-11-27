"""Where a session runs and how flowkit reaches it there.

A session runs in a tmux pane (driver=tmux), as one of Claude Code's own background sessions
(driver=background, `claude --bg`), or in a place of another program, reached through an
executable of the user's own (driver=<name>, `flowkit-driver-<name>`: the class Driver). Whatever
finds a session, reads what it shows or types into it asks a backend here, and each answers the
same requests:

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
  places()                    every place it has, a session in it or not, each as a dict
  label(states, groups)       the word the user sees beside each place and each group of places
  close(place)                the place goes, with what runs in it
  pick(place, client)         the user's terminal turns to it
  target()                    where a launch from this process goes, as one word of the backend's
                              own: a process started apart from it (a queued launch, a watch's
                              poller) hands the word back in FLOWKIT_TARGET and reaches the same
                              place. "" for a backend that needs none
  plan(launch, listed)        what open() would do with a Launch, nothing started yet: `reporter`
                              says where the story's reporter would start ("" when the story has
                              one), `show(launch)` gives its own lines of a dry run. `listed` is
                              Claude Code's list as the caller last read it
  open(launch, plan, say, again, dialog)
                              starts the session, the story's reporter before it where the plan
                              says so, and gives each as an Opened once it is up or its wait is
                              over. `say` takes a warning's line; `again(name)` is asked before a
                              launch is typed a second time and raises when it must not be;
                              `dialog(text)` says which dialog a screen ends on

plan() and open() raise Refused where nothing is started: `why` says which case it is.

A request a backend has no answer to gets the empty one (no screen, no story, nothing parked), so
a caller asks without knowing which it holds. A program that is missing or hangs raises OSError
or subprocess.SubprocessError: the caller says what that means where it is. A place may also be a
bare pane id for TMUX, known some other way than through locate.

The same for all, read from Claude Code's own list: agents() and mode(row), which says busy,
waiting or idle.

Which backend: driver(name) gives the one of that name, the one `driver=` in handoff.conf names
when no name is given (tmux without the line), None when nothing answers to the name; terminal()
gives the one a session with a terminal of its own runs in, tmux unless handoff.conf names an
outside driver.

What flowkit knows of a session and no backend has to keep for it is in one file, STATE: kept()
reads it, keep(change) changes it under its lock, register() writes a session's own entry at its
start, note() adds to one, registered() finds the entry of a place. A launch writes there what a
place was opened for before a session runs in it (launched()), and the session takes that over as
it registers.

This file imports nothing of the plugin, so a command copied out of it loads it by path.
"""
import collections
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import time
import types
from pathlib import Path

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

STARTING = 15  # seconds a launch waits for its session to be listed
LINKING = 30  # seconds more for the Remote Control link alone
SLOTS = 9  # sessions tiled in one tab
ASKING = 10  # seconds an outside driver has for one request
ABOVE = 8  # processes looked through from a session up to the one an outside driver started in its place

SEP = "\x1f"
STATE = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "flowkit" / "sessions.json"
GONE = 86400  # seconds an entry outlives its session's process: a stopped session that starts again registers again
CONF = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "flowkit" / "handoff.conf"
NAMED = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*$")  # a driver's name: what its executable's name ends on

PROMPT_LINE = re.compile(r"^[│|\s]*[>❯]\s?(.*?)[\s│|]*$")
# what `capture-pane -e` adds to a row: a change of attributes (its codes are group 1), a link, any other sequence
SEQUENCE = re.compile(r"\x1b\[([\d;:]*)m|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[^@-~]*[@-~]")
COLOUR = re.compile(r"\b[345]8;(?:5;\d*|2;\d*;\d*;\d*)")  # a colour's own numbers are no attributes
LINK = re.compile(r"https://claude.ai/code/session_[A-Za-z0-9]*")  # a session's Remote Control link
SHELLS = ("zsh", "bash", "fish", "sh", "-zsh", "-bash", "-fish", "-sh")  # what a pane with nothing started in it runs
DIALOG = re.compile(r"enter to (?:confirm|select|continue)|esc to (?!interrupt)\w+|^\s*(?:[❯>]\s*)?\d+\.\s", re.I | re.M)


def aimed():
    """The tmux server and session a caller outside tmux names: FLOWKIT_TMUX_SOCKET and
    FLOWKIT_TMUX_SESSION, else the two halves of FLOWKIT_TARGET, the word Tmux.target() gave."""
    sock, _, sess = os.environ.get("FLOWKIT_TARGET", "").partition("/")
    return os.environ.get("FLOWKIT_TMUX_SOCKET") or sock, os.environ.get("FLOWKIT_TMUX_SESSION") or sess


def tmux(*args, env=None):
    sock = aimed()[0]
    return subprocess.run(
        ["tmux", *(["-L", sock] if sock else []), *args], capture_output=True, text=True, timeout=10, env=env
    )


def claude(*args):
    """What `claude <args>` prints, "" where it is missing or hangs."""
    try:
        return subprocess.run(["claude", *args], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


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


def stuck(found):
    """Why the prompt box of a screen takes no line, "" when it does: no prompt box (none on
    screen), dialog (rows of one sit below it: a menu's numbered options, its Enter or Esc hint)
    or draft. Only rows below the last prompt-box line count, so a dialog quoted in the
    conversation above it never does."""
    last = max((i for i, row in enumerate(found) if PROMPT_LINE.match(row[0])), default=None)
    if last is None:
        return "no prompt box"
    if DIALOG.search("\n".join(row[0] for row in found[last + 1:])):
        return "dialog"
    return "draft" if drafted(found[last]) else ""


def cleaned():
    """The environment a launch's place is made with. A place gets the PATH of the process that
    makes it: called from a session, that PATH holds the session's plugin bin/ directories,
    which the session started in the place would keep ahead of its own, so an older release's
    commands would answer there. A launch goes without them."""
    path = os.environ.get("PATH", "")
    clean = os.pathsep.join(p for p in path.split(os.pathsep) if not re.search(r"/plugins/cache/.*/bin/*$", p))
    return {**os.environ, "PATH": clean or path}


class Typed(str):
    """type()'s answer: "" once typed, else why not. `said` is what the backend's program printed
    while it typed, for a caller that shows it."""
    said = ""


class Refused(Exception):
    """A launch that is not started; the text says it in the backend's own words. `why`: outside
    (this process runs outside the backend and names no target), nowhere (the target has nothing
    to launch in), busy (the place asked for runs something), untrusted (Claude Code was never
    started in the folder), start (the session's program refused the start), exited (it exited at
    its start, twice), failed (the backend's program refused a step)."""

    def __init__(self, why, said=""):
        super().__init__(said)
        self.why = why


class Launch(types.SimpleNamespace):
    """What plan() and open() are asked for. `story`: the group the session joins, a story's slug
    or, with `adhoc`, the shared one of the one-offs; `name`, the session's; `cwd`; `args`: what
    follows `claude` on its command line, as shell text; `rc`: a Remote Control link is waited
    for; `reporter` and `reporter_args`: the name of the story's reporter, started first where the
    story has none, and its arguments; `place`: this place, an idle one, instead of the group's
    next; `fresh`: never a place that is there already; `target`: the word target() gave;
    `program`: what is started in a terminal's place, `claude` unless a driver is being tried."""
    args = reporter = reporter_args = place = target = ""
    program = "claude"
    adhoc = fresh = False
    rc = True


class Opened(types.SimpleNamespace):
    """A session open() started. `name`; `place`; `where`: the place in the backend's own words,
    for the launcher's line; `up`: Claude Code lists it; `rc`: its Remote Control link, pending or
    off. Where the launch was typed into a place: `created` (1 when the place was made for it),
    `tried` (1, or 2 when it was typed a second time) and `parked` (the first line of the start-up
    dialog it sits at)."""
    created = tried = 0
    parked = ""


def running(backend, name, listed):
    """Where a session of that name runs: "claude agents" when Claude Code lists it, the id of a
    place that shows the name as its title when it does not, "" when nothing does. The place this
    process runs in is never it."""
    if any(isinstance(a, dict) and a.get("name") == name for a in listed):
        return "claude agents"
    here = backend.here()
    for place in backend.places():
        title = re.sub(r"^[^0-9A-Za-z]+", "", (place.get("title") or "").strip())
        if place["id"] != (here.id if here else "") and place.get("cmd") not in SHELLS and title == name:
            return place["id"]
    return ""


def kept():
    """What the file holds: {"sessions": {session id: entry}, "places": {"<backend> <place id>":
    entry}}, both empty without a file. A session's entry: `backend` and `place` (where it runs),
    `story` (the one it was started for), `adhoc` (a one-off outside any story), `pid`, `at` (its
    last write), and what was noted for it. A place's entry is what is known of a place that has
    no session yet: `story`, `adhoc` and `born` (when the launch was typed) from the launch into
    it, `parked` (when a start-up dialog was first seen there)."""
    try:
        doc = json.loads(STATE.read_text())
    except (OSError, ValueError):
        doc = {}
    found = {}
    for part in ("sessions", "places"):
        entries = doc.get(part) if isinstance(doc, dict) else None
        found[part] = {k: v for k, v in entries.items() if isinstance(v, dict)} if isinstance(entries, dict) else {}
    return found


def keep(change):
    """Changes the file: `change` gets what is kept and edits it in place. One writer at a time (a
    lock beside the file) and the file replaced whole, so a reader never sees half of it and two
    writers never lose each other's entry. Returns what is kept now."""
    STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE.with_suffix(".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        doc = kept()
        change(doc)
        fresh = STATE.with_suffix(".tmp")
        fresh.write_text(json.dumps(doc))
        os.replace(fresh, STATE)
    return doc


def alive(pid):
    try:
        os.kill(int(pid), 0)
    except PermissionError:
        return True
    except (OSError, ValueError, TypeError, OverflowError):
        return False
    return True


def register(sid, backend, place, story="", adhoc=False, pid=None, inside=()):
    """A session's own entry, written at its start (a resume is one), or None: the place is the
    session's whose process is one of `inside`, the processes this one was started from (a
    `claude -p` a session runs), and it registers nothing. What was noted for the session stays.
    Another session's entry at the same place goes, since that session has left it, and so does
    any entry whose process went more than a day ago. What a launch wrote for the place is the
    session's from here on: its story and its launch stamp, in place of the ones given."""
    now = time.time()

    def change(doc):
        entries = doc["sessions"]
        for other, entry in list(entries.items()):
            if other == sid:
                continue
            if (entry.get("backend"), entry.get("place")) == (backend, place):
                if entry.get("pid") in inside:
                    return
                del entries[other]
            elif now - (entry.get("at") or 0) > GONE and not alive(entry.get("pid")):
                del entries[other]
        entries[sid] = {**entries.get(sid, {}), "backend": backend, "place": place, "story": story, "adhoc": adhoc,
                        "pid": pid, "at": int(now)}
        record = doc["places"].pop(f"{backend} {place}", None) or {}
        if "story" in record:
            entries[sid].update(story=record["story"] or "", adhoc=bool(record.get("adhoc")), born=record.get("born"))
    return keep(change)["sessions"].get(sid)


def note(sid, **fields):
    """Adds to a session's entry, registered or not."""
    def change(doc):
        doc["sessions"][sid] = {**doc["sessions"].get(sid, {}), **fields, "at": int(time.time())}
    keep(change)


def launched(backend, place, story, adhoc=False):
    """A launch into a place, written before its session runs: what the place was opened for and
    when. It replaces what was known of the place, a start-up dialog seen for its last session
    too. A file that cannot be written stops no launch."""
    def change(doc):
        doc["places"][f"{backend} {place}"] = {"story": "" if adhoc else story, "adhoc": bool(adhoc), "born": int(time.time())}
    try:
        keep(change)
    except OSError:
        pass


def registered(backend, place):
    """The entry of the session registered at a place, None without one."""
    return next((e for e in kept()["sessions"].values() if (e.get("backend"), e.get("place")) == (backend, place)), None)


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
        entry = registered(self.name, place.id if isinstance(place, Place) else place)
        return entry.get("story") or "" if entry else ""

    def last_input(self):
        return None

    def places(self):
        return []

    def label(self, states, groups):
        return None

    def close(self, place):
        return False

    def pick(self, place, client=""):
        return False

    def target(self):
        return ""

    def plan(self, launch, listed=None):
        raise Refused("nowhere", "nothing to launch in")

    def open(self, launch, plan, say=None, again=None, dialog=None):
        raise Refused("nowhere", "nothing to launch in")


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
        """None too when another server is named than the one this process runs in: pane ids
        repeat across servers."""
        pane, sock = os.environ.get("TMUX_PANE"), aimed()[0]
        if sock and os.path.basename(os.environ.get("TMUX") or "x").split(",")[0] != sock:
            return None
        return Place(pane, False, None) if pane else None

    def screen(self, place):
        return rows(tmux("capture-pane", "-p", "-e", "-t", self.pane(place)).stdout)

    def bottom(self, place):
        found = tmux("capture-pane", "-p", "-t", self.pane(place)).stdout.splitlines()
        while found and not found[-1].strip():
            found.pop()
        return found[-BOTTOM:]

    def parked(self, place):
        return stuck(self.screen(place))

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
        """What the last launch into the pane wrote while no session has registered there since,
        else the story of the session registered there, else what an older launch left."""
        record = kept()["places"].get(f"{self.name} {self.pane(place)}") or {}
        if "story" in record:
            return record["story"] or ""
        entry = registered(self.name, self.pane(place))
        return entry.get("story") or "" if entry else self.marked(place)[0]

    def last_input(self):
        seen = [int(x) for x in tmux("list-clients", "-F", "#{client_activity}").stdout.split() if x.isdigit()]
        return max(seen) if seen else None

    def marked(self, place):
        """What the tab a pane sits in was opened for, as the launch marked it: (story, adhoc). The
        mark is the tab's place in the layout, which plan() reads to find a story's tabs. What a
        session is filed under is in STATE since 0.15.1: a tab an older launch opened, and a
        session that started before 0.15.0 and registered nothing, are read from here until the
        first minor after 2026-12-11."""
        story, _, adhoc = tmux("display-message", "-p", "-t", self.pane(place),
                               f"#{{@flowkit_story}}{SEP}#{{@flowkit_adhoc}}").stdout.strip("\n").partition(SEP)
        return story, bool(adhoc)

    def places(self):
        """Every pane as a dict. `id`; `pid`, its shell's; where it is: `sess`, its tab's `wid`,
        `widx` and `wname`, its `pidx` in the tab; `cmd`, what runs in it (a shell's name, Claude
        Code's version). And what tmux keeps for it: `story` and `slug`, what its tab was opened
        for (`slug` is empty in the tab of the one-offs); `hold`, the user's own switch that keeps
        the tab; `born`, when a launch was last typed into it or its tab was opened; `rung` and
        `parked`, which a tick before 0.15.0 kept here, read where STATE has none until the first
        minor after 2026-12-11;
        `title`, what the pane shows as its name. `story`, `slug` and `born` are the ones of the
        last launch into the pane where STATE has it, tmux's for a tab an older launch opened."""
        fmt = SEP.join(["#{pane_pid}", "#{pane_id}", "#{session_name}", "#{window_id}", "#{window_index}",
                        "#{window_name}", "#{pane_index}", "#{pane_current_command}",
                        "#{@flowkit_story}#{@flowkit_adhoc}", "#{@flowkit_story}", "#{@flowkit_hold}#{@cc_hold}",
                        "#{@flowkit_born}", "#{@flowkit_rung}", "#{@flowkit_parked}", "#{pane_title}"])
        keys = ("pid", "id", "sess", "wid", "widx", "wname", "pidx", "cmd", "story", "slug", "hold", "born", "rung",
                "parked", "title")
        found = [dict(zip(keys, line.split(SEP))) for line in tmux("list-panes", "-a", "-F", fmt).stdout.splitlines() if line]
        doc = kept()
        sessions = {e.get("place"): e for e in doc["sessions"].values() if e.get("backend") == self.name}
        for pane in found:
            record = doc["places"].get(f"{self.name} {pane['id']}") or {}
            if "story" in record:
                pane["slug"] = record["story"] or ""
                pane["story"] = pane["slug"] or ("adhoc" if record.get("adhoc") else "")
            born = [int(at) for at in (record.get("born"), (sessions.get(pane["id"]) or {}).get("born"), pane.get("born"))
                    if str(at or "").isdigit()]
            if born:
                pane["born"] = str(max(born))
        return found

    def label(self, states, groups):
        """`states`: {pane: its word} for each pane that shows one in its border; `groups`: {tab:
        (how many of its sessions wait on the user, the tab's word)}. What is not named loses its
        label. Only what differs from what is shown is written, in one call."""
        def write(scope, at, option, value):
            return [";", "set-option", scope, *(() if value else ("-u",)), "-t", at, option, *((value,) if value else ())]
        shown = tmux("list-panes", "-a", "-F", f"p{SEP}#{{pane_id}}{SEP}#{{@flowkit_state}}", ";", "list-windows", "-a",
                     "-F", f"w{SEP}#{{window_id}}{SEP}#{{@flowkit_wait}}{SEP}#{{@flowkit_tab}}").stdout
        cmds = []
        for line in shown.splitlines():
            kind, at, state, word = (line.split(SEP) + ["", "", ""])[:4]
            if kind == "p" and states.get(at, "") != state:
                cmds += write("-p", at, "@flowkit_state", states.get(at, ""))
            elif kind == "w":
                n, want = groups.get(at, (0, ""))
                if (str(n) if n else "") != state:
                    cmds += write("-w", at, "@flowkit_wait", str(n) if n else "")
                if want != word:
                    cmds += write("-w", at, "@flowkit_tab", want)
        if cmds:
            tmux(*cmds[1:])

    def close(self, place):
        return tmux("kill-pane", "-t", self.pane(place)).returncode == 0

    def pick(self, place, client=""):
        return tmux("switch-client", *(["-c", client] if client else []), "-t", self.pane(place)).returncode == 0

    def session(self):
        """The tmux session the terminal this runs in is attached to."""
        return tmux("display-message", "-p", "#{session_name}").stdout.strip()

    @staticmethod
    def ask(*args):
        """tmux for a launch: what it printed, Refused when it refuses. tmux starts a pane a
        process creates with that process's PATH, not the server's, so a launch calls it with
        cleaned()'s."""
        done = tmux(*args, env=cleaned())
        if done.returncode:
            raise Refused("failed", done.stderr.strip() or f"tmux {args[0]} failed")
        return done.stdout.rstrip("\n")

    def target(self):
        """`<server>/<session>`: the server as `tmux -L` names it and the session in it whose tabs a
        launch joins. The ones a caller names (aimed()), else the session of the pane this runs
        in, else the server's first."""
        sock, sess = aimed()
        if not sock and not os.environ.get("TMUX"):
            raise Refused("outside", "not inside tmux")
        if not sess:
            here = self.here()
            sess = self.ask("display-message", "-p", "-t", here.id, "#{session_name}") if here else \
                (tmux("list-sessions", "-F", "#{session_name}").stdout.splitlines() or [""])[0]
        if not sess:
            raise Refused("nowhere", "no tmux session to launch in" + (f" on server '{sock}'" if sock else ""))
        return f"{sock or os.path.basename(os.environ.get('TMUX', '').split(',')[0])}/{sess}"

    def plan(self, launch, listed=None):
        """The pane: the one given; else an idle shell or a free slot in the story's tabs, in the
        order of their parts; else a new tab `<story>[-<part>]` after the last tab. The reporter:
        whenever the story has none running, in slot 1 of a new tab, else in a pane after the
        last. `pane` is "new" for one still to be made."""
        key = "@flowkit_adhoc" if launch.adhoc else "@flowkit_story"
        sess, here = launch.target.partition("/")[2], self.here()
        plan = types.SimpleNamespace(sess=sess, key=key, pane=launch.place, win="", tab="", part=1, claim="", reporter="")
        if launch.place:
            cmd = self.ask("display-message", "-p", "-t", launch.place, "#{pane_current_command}")
            if cmd not in SHELLS:
                raise Refused("busy", f"pane {launch.place} is running '{cmd}', not an idle shell")
            # A tab opened by hand for this one pane becomes the story's next tab: the closer may close it
            # once the story is finished, and the story's next launch finds it. A tab with other panes in
            # it stays the user's: the closer closes whole tabs.
            plan.win, panes, marked = (self.ask("display-message", "-p", "-t", launch.place,
                                                "#{window_id} #{window_panes} #{@flowkit_story}#{@flowkit_adhoc}").split(None, 2) + ["", ""])[:3]
            if not launch.adhoc and panes == "1" and not marked:
                parts = [f.split() for f in self.ask("list-windows", "-t", sess, "-F", f"#{{{key}_part}} #{{{key}}}").splitlines()]
                plan.part = max((int(f[0]) for f in parts if len(f) > 1 and f[1] == launch.story and f[0].isdigit()), default=0) + 1
                plan.claim = plan.win
        else:
            tabs = [f.split() for f in tmux("list-windows", "-t", sess, "-F",
                                            f"#{{{key}_part}} #{{window_id}} #{{window_panes}} #{{{key}}}").stdout.splitlines()]
            parts = 0
            for part, win, panes in sorted((int(f[0]), f[1], int(f[2])) for f in tabs if len(f) > 3 and f[3] == launch.story
                                           and f[0].isdigit() and f[2].isdigit()):
                parts = part
                if plan.pane:
                    continue
                idle = "" if launch.fresh else self.idle(win, here)
                if idle:
                    plan.pane, plan.win = idle, win
                elif panes < SLOTS:
                    plan.pane, plan.win = "new", win
            if not plan.pane:
                plan.part = parts + 1
                plan.tab = launch.story if plan.part == 1 else f"{launch.story}-{plan.part}"
                plan.pane = "new"
        if launch.reporter and not running(self, launch.reporter, listed or []):
            plan.reporter = "in slot 1" if plan.tab else "in a pane after the last"

        def show(launch):
            tab = f"new tab {plan.tab} after the last one" if plan.tab else self.ask("display-message", "-p", "-t", plan.win, "#{window_name}")
            return [("tab", tab + (f" (becomes tab {plan.part} of the story)" if plan.claim else "")),
                    ("pane", "<new, tiled after the last pane>" if plan.pane == "new" else plan.pane),
                    ("send", f"cd '{launch.cwd}' && clear; {launch.program} {launch.args}")]
        plan.show = show
        return plan

    @staticmethod
    def idle(win, here):
        """The first idle shell pane of a window that is not the pane this runs in."""
        for line in tmux("list-panes", "-t", win, "-F", "#{pane_id} #{pane_current_command} #{pane_dead} #{pane_in_mode}").stdout.splitlines():
            pane, cmd, dead, mode = (line.split() + ["", "", "", ""])[:4]
            if pane != (here.id if here else "") and dead == "0" and mode == "0" and cmd in SHELLS:
                return pane
        return ""

    def split(self, win, cwd):
        """A new pane after the window's last one, so tiling keeps launch order."""
        last = self.ask("list-panes", "-t", win, "-F", "#{pane_id}").splitlines()[-1]
        pane = self.ask("split-window", "-d", "-t", last, "-c", cwd, "-P", "-F", "#{pane_id}")
        self.ask("select-layout", "-t", win, "tiled")
        return pane

    def mark(self, win, plan, launch):
        """Marks a window as the story's tab. The launch stamp comes first, in the same command: the
        story closer runs at a status-line redraw where no timer runs the tick, and opening a window
        is one, so it lists the panes within milliseconds and must never see a story tab of bare
        shells without a stamp. Each pane's top border names its slot, its session and the state
        the tick sets."""
        self.ask("set-option", "-w", "-t", win, "@flowkit_born", str(int(time.time())),
                 ";", "set-option", "-w", "-t", win, plan.key, launch.story,
                 ";", "set-option", "-w", "-t", win, f"{plan.key}_part", str(plan.part),
                 ";", "set-option", "-w", "-t", win, "pane-border-status", "top",
                 ";", "set-option", "-w", "-t", win, "pane-border-format",
                 " #{pane_index} #{s/^[^a-zA-Z0-9]+//:pane_title}#{?#{@flowkit_state}, · #{?#{==:#{@flowkit_state},waiting},"
                 "#[reverse] NEEDS YOU #[noreverse],#{@flowkit_state}},} ")

    def open(self, launch, plan, say=None, again=None, dialog=None):
        """Makes the panes the plan names, then starts the reporter and the session in them, one
        after the other. A pane made here is the story's from that moment (launched()), before
        anything is typed into it."""
        pane, win, reporter, created = plan.pane, plan.win, "", 0
        if plan.tab:
            last = max((int(i) for i in self.ask("list-windows", "-t", plan.sess, "-F", "#{window_index}").split() if i.isdigit()),
                       default=0)
            win = self.ask("new-window", "-d", "-t", f"{plan.sess}:{last + 1}", "-n", plan.tab, "-c", launch.cwd, "-P", "-F",
                           "#{window_id}")
            self.mark(win, plan, launch)
            pane = self.ask("list-panes", "-t", win, "-F", "#{pane_id}").splitlines()[0]
            if plan.reporter:
                reporter, pane = pane, self.split(win, launch.cwd)
            created = 1
        elif pane == "new":
            reporter = self.split(win, launch.cwd) if plan.reporter else ""
            pane, created = self.split(win, launch.cwd), 1
        else:
            if plan.claim:
                self.mark(plan.claim, plan, launch)
            reporter = self.split(win, launch.cwd) if plan.reporter else ""
        for made in ([reporter] if reporter else []) + ([pane] if created else []):
            launched(self.name, made, launch.story, launch.adhoc)
        # The reporter's answer comes before the session starts: a session that exits twice still
        # leaves a caller knowing that a reporter runs.
        tab = self.ask("display-message", "-p", "-t", pane, "#{window_name}")
        if reporter:
            yield self.start(reporter, launch.reporter, launch.reporter_args, tab, launch, 0, say, again, dialog)
        yield self.start(pane, launch.name, launch.args, tab, launch, created, say, again, dialog)

    def typed(self, pane, args, launch):
        """The launch, typed into the pane's shell. The stamp in the pane is for a tick of 0.15.0,
        which reads the launch from there; what an older tick kept of the pane's last session
        goes with it."""
        launched(self.name, pane, launch.story, launch.adhoc)
        self.ask("set-option", "-p", "-t", pane, "@flowkit_born", str(int(time.time())),
                 ";", "set-option", "-p", "-u", "-t", pane, "@flowkit_parked")
        self.ask("send-keys", "-t", pane, "C-u")  # anything half-typed would prefix the command
        self.ask("send-keys", "-t", pane, f"cd '{launch.cwd}' && clear; {launch.program} {args}", "Enter")

    def start(self, pane, name, args, tab, launch, created, say, again, dialog):
        """Types the launch into a pane, then waits up to 15 s until Claude Code lists a session of
        that name under the pane's shell and Remote Control has printed its link, 30 s more for the
        link alone. A pane that is a bare shell again within the 15 s with no session of the name
        listed means claude exited at start-up, so the launch is typed once more into that pane,
        once `again` let it; a second exit is Refused, the output left in the pane. A pane still
        running something unlisted at 15 s (a prompt, a slow start) gets the warning, never a
        second launch; when its screen ends on a dialog the warning and `parked` carry the
        dialog's first line. A dialog is never answered here: what it asks is the user's to
        decide. Never types /remote-control: with the flag passed that opens the Remote Control
        panel, which holds back background-task results while open."""
        def shown(*more):
            return tmux("capture-pane", "-p", "-J", *more, "-t", pane).stdout

        shell = self.ask("display-message", "-p", "-t", pane, "#{pane_pid}")
        link, tried, up, seen, i = None, 1, False, False, 0
        self.typed(pane, args, launch)
        while i < STARTING:
            i += 1
            time.sleep(1)
            screen = shown("-S", "-80")
            try:
                listed, above = agents(), parents()
            except (OSError, ValueError, subprocess.SubprocessError):
                listed, above = [], {}
            up = up or any(isinstance(a, dict) and a.get("name") == name and a.get("pid")
                           and str(above.get(a["pid"])) == shell for a in listed)
            link = LINK.search(screen)
            if up and (link or not launch.rc):
                break
            if up:
                continue
            # The shell again, once claude was seen running or from the third second on (right after
            # Enter the pane can still read as the shell), and the launch no longer on the screen (a
            # shell slow to start has it typed ahead, unread; `clear` wipes it once it runs).
            if self.ask("display-message", "-p", "-t", pane, "#{pane_current_command}") not in SHELLS:
                seen = True
                continue
            if not (seen or i >= 3) or f"--name '{name}'" in shown():
                continue
            if tried == 2:
                raise Refused("exited", f"session '{name}' exited twice at start-up in pane {pane}; its output is in the pane")
            if again:
                again(name)
            if say:
                say(f"warning: session '{name}' exited at start-up in {pane}; typed again")
            tried, i, seen = 2, 0, False
            self.typed(pane, args, launch)
        if up and launch.rc and not link:
            for _ in range(LINKING):
                time.sleep(1)
                link = LINK.search(shown("-S", "-80"))
                if link:
                    break
        parked = ""
        if not up:
            parked = dialog(tmux("capture-pane", "-p", "-t", pane).stdout) if dialog else ""
            if say and parked:
                say(f"warning: session '{name}' is parked at a start-up prompt in {pane} ({parked}); answer it in the pane")
            elif say:
                say(f"warning: no session '{name}' came up in {pane} within 15s; check the pane (a folder trust prompt?)")
        index = self.ask("display-message", "-p", "-t", pane, "#{pane_index}")
        return Opened(name=name, place=pane, where=f"pane={index} id={pane} tab={tab}", up=up, created=created, tried=tried,
                      rc=(link.group(0) if link else "pending") if launch.rc else "off", parked=parked)


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
        answer = Typed("" if sent.returncode == 0 else "not typed")
        answer.said = (sent.stdout or "") + (sent.stderr or "")
        return answer

    def plan(self, launch, listed=None):
        """The reporter: one per story, kept between closes. A stopped one is woken by the next
        ring, so only a story that never had one gets a new one."""
        try:
            known = agents("--all")
        except (OSError, ValueError, subprocess.SubprocessError):
            known = []
        first = bool(launch.reporter) and not any(isinstance(a, dict) and a.get("name") == launch.reporter for a in known)
        return types.SimpleNamespace(reporter="first" if first else "", show=lambda launch: [
            ("driver", self.name), ("run", f"cd '{launch.cwd}' && claude --bg {launch.args}")])

    def open(self, launch, plan, say=None, again=None, dialog=None):
        if plan.reporter:
            yield self.start(launch.reporter, launch.reporter_args, launch, say)
        yield self.start(launch.name, launch.args, launch, say)

    def start(self, name, args, launch, say):
        """`claude --bg` hands the session to Claude's background service and prints its id. It
        refuses at once in a folder never trusted; it never waits at the prompt. Confirmed when
        Claude Code lists it, which is also when its story is written under its session id; the
        Remote Control link shows in its log."""
        done = subprocess.run(["bash", "-c", f"claude --bg {args}"], cwd=launch.cwd, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True)
        said = done.stdout.rstrip("\n")
        if done.returncode:
            raise Refused(*(("untrusted", said) if "not trusted" in said else ("start", f"claude --bg failed:\n{said}")))
        found = re.search(r"^backgrounded · ([0-9a-f]+)", said, re.M)
        if not found:
            raise Refused("start", f"claude --bg printed no session id:\n{said}")
        short, row = found.group(1), None
        for _ in range(STARTING):
            try:
                row = next((a for a in agents() if isinstance(a, dict) and a.get("id") == short), None)
            except (OSError, ValueError, subprocess.SubprocessError):
                row = None
            if row:
                break
            time.sleep(1)
        if row and row.get("sessionId"):
            try:
                register(row["sessionId"], self.name, row["sessionId"], "" if launch.adhoc else launch.story, launch.adhoc,
                         row.get("pid"))
            except OSError:
                pass
        elif not row and say:
            say(f"warning: background session '{name}' ({short}) is not listed by claude agents after 15s; "
                f"check claude logs {short}")
        link = None
        for _ in range(LINKING if launch.rc else 0):
            link = LINK.search(claude("logs", short))
            if link:
                break
            time.sleep(1)
        return Opened(name=name, place=short, where=f"driver={self.name} id={short}", up=bool(row),
                      rc=(link.group(0) if link else "pending") if launch.rc else "off")


class Driver(Backend):
    """A session in a place of a program flowkit does not know (a window, a tab, a pane of some
    multiplexer or terminal), reached through the executable `flowkit-driver-<name>`. It is run
    as `<executable> <request> [argument ...]` and answers on its standard output with exit 0;
    exit 2 says it has no answer to that request, any other exit that it failed, the reason on
    its standard error. Its requests (docs/drivers.md has them in full), the first three
    required:

      open <story> <name> <folder> <command>   a new place for that session of the story, the shell command started
                                               in it in that folder; prints the place's id
      list                                     every place it has, a line each: the id, a tab, the pid of the process
                                               it started there
      type <id> <text>                         the characters reach what runs there as if typed, control ones too
      screen <id>                              what the place shows, as text
      label <id> <word>                        the word the user sees beside it, none for an empty one
      close <id>                               the place goes, with what runs in it
      pick <id>                                the user's terminal turns to it

    The rest is flowkit's, here: a session is found through the processes above it, up to the
    one the driver started in its place; a line is its characters, then Enter after the same
    pause; what a prompt box holds is read from the screen; a launch is waited for by looking at
    Claude Code's list and at the driver's."""

    def __init__(self, name, path):
        self.name, self.program, self.path = name, f"flowkit-driver-{name}", path
        self.silent = set()  # the requests it has said it has no answer to: each is asked once

    @staticmethod
    def at(place):
        return place.id if isinstance(place, Place) else place

    def available(self):
        return os.access(self.path, os.X_OK)

    def ask(self, request, *args, env=None):
        """A request to the executable: (True, what it printed), (False, why it failed) or
        (None, "") where it has no answer to the request."""
        if request in self.silent:
            return None, ""
        done = subprocess.run([self.path, request, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              errors="replace", timeout=ASKING, env=env)
        if done.returncode == 2:
            self.silent.add(request)
            return None, ""
        return (True, done.stdout.rstrip("\n")) if done.returncode == 0 else (False, done.stderr.strip())

    def listed(self):
        """{a place's id: the pid of what the driver started there, None where it names none};
        None when it gave no list."""
        answered, said = self.ask("list")
        if not answered:
            return None
        found = {}
        for line in said.splitlines():
            at, _, pid = line.partition("\t")
            if at.strip():
                found[at.strip()] = int(pid) if pid.strip().isdigit() else None
        return found

    def finder(self):
        """As Tmux.finder: one reading of the places and of the processes, as a function from a
        session's pid to its place."""
        tops = {pid: at for at, pid in (self.listed() or {}).items() if pid}
        above = parents()

        def find(pid):
            for _ in range(ABOVE):
                if pid in tops:
                    return Place(tops[pid], False, None)
                pid = above.get(pid)
                if pid is None:
                    return None
            return None
        return find

    def locate(self, pid=None, sid=None, name=None):
        return self.finder()(pid) if pid else None

    def here(self):
        return self.finder()(os.getpid())

    def shown(self, place):
        """What the driver prints for a place's screen, "" where it shows none."""
        answered, said = self.ask("screen", self.at(place))
        return said if answered else ""

    def screen(self, place):
        return rows(self.shown(place))

    def bottom(self, place):
        found = [text for text, _ in self.screen(place)]
        while found and not found[-1].strip():
            found.pop()
        return found[-BOTTOM:]

    def parked(self, place):
        """As in a tmux pane, read from the screen the driver shows; "" where it shows none, since
        nothing is known to be in the line's way."""
        found = self.screen(place)
        return stuck(found) if found else ""

    def draft(self, place):
        found = [row for row in self.screen(place) if PROMPT_LINE.match(row[0])]
        return bool(found) and bool(drafted(found[-1]))

    def type(self, place, text, how=LINE, settled=None):
        """The line's characters, then Enter (a carriage return) after the pause, each a request of
        its own; Ctrl-U before the line clears what is half typed, Escape goes first as its own
        character. Why not: parked's (FREE, COMMAND, where the driver shows a screen), not
        settled (ESCAPE, as for tmux), not typed (the driver failed)."""
        at = self.at(place)
        if how in (FREE, COMMAND):
            why = self.parked(place)
            if why:
                return why
        if how == ESCAPE:
            self.ask("type", at, "\x1b")
            if settled is not None and not settled():
                return "not settled"
            time.sleep(PAUSE)
        if not self.ask("type", at, ("\x15" if how in (OVER, ESCAPE) else "") + text)[0]:
            return "not typed"
        time.sleep(PAUSE)
        return "" if self.ask("type", at, "\r")[0] else "not typed"

    def escape(self, place):
        return bool(self.ask("type", self.at(place), "\x1b")[0])

    def story(self, place):
        """What the last launch into the place wrote while no session has registered there
        since, else the story of the session registered there."""
        record = kept()["places"].get(f"{self.name} {self.at(place)}") or {}
        return record["story"] or "" if "story" in record else super().story(place)

    @staticmethod
    def marked(place):
        """A place carries no mark of its own: what it was opened for is in STATE alone."""
        return "", False

    def places(self):
        """Every place the driver lists, as a dict with the keys of a tmux pane. `id`; `pid`, of
        what the driver started there; `story`, `slug` and `born` from STATE (the last launch into
        it, else the session registered there). A place is its own group of one: `wid` is its
        id and `wname` its story. What only tmux knows is empty."""
        doc = kept()
        sessions = {e.get("place"): e for e in doc["sessions"].values() if e.get("backend") == self.name}
        found = []
        for at, pid in (self.listed() or {}).items():
            record, entry = doc["places"].get(f"{self.name} {at}") or {}, sessions.get(at) or {}
            known = record if "story" in record else entry
            slug = known.get("story") or ""
            born = [int(x) for x in (record.get("born"), entry.get("born")) if str(x or "").isdigit()]
            found.append({"id": at, "pid": str(pid or ""), "sess": "", "wid": at, "widx": "", "wname": slug or "-", "pidx": "",
                          "cmd": "", "story": slug or ("adhoc" if known.get("adhoc") else ""), "slug": slug, "hold": "",
                          "born": str(max(born)) if born else "", "rung": "", "parked": "", "title": ""})
        return found

    def label(self, states, groups):
        """Each place is told its word, an empty one where it shows none: what a driver shows
        cannot be read back, so all are told at every call. A group has no label of its own.
        False when the driver failed at one."""
        there = self.listed()
        told = [self.ask("label", at, states.get(at, ""))[0] for at in there or {}]
        return False if there is None or False in told else None

    def close(self, place):
        return bool(self.ask("close", self.at(place))[0])

    def pick(self, place, client=""):
        return bool(self.ask("pick", self.at(place))[0])

    def plan(self, launch, listed=None):
        """Nothing to pick: where a new place goes is the driver's. The reporter: whenever the
        story has none running."""
        first = bool(launch.reporter) and not running(self, launch.reporter, listed or [])
        return types.SimpleNamespace(reporter="first" if first else "", show=lambda launch: [
            ("driver", self.name),
            ("run", shlex.join([self.program, "open", launch.story, launch.name, launch.cwd, f"{launch.program} {launch.args}"]))])

    def open(self, launch, plan, say=None, again=None, dialog=None):
        if plan.reporter:
            yield self.start(launch.reporter, launch.reporter_args, launch, say, again, dialog)
        yield self.start(launch.name, launch.args, launch, say, again, dialog)

    def make(self, name, args, launch):
        """A new place with the session's command started in it, the story's from that moment
        (launched()); Refused when the driver made none."""
        answered, said = self.ask("open", launch.story, name, launch.cwd, f"{launch.program} {args}", env=cleaned())
        place = said.strip() if answered else ""
        if not place:
            raise Refused("failed", (said if answered is False else "") or f"{self.program} open gave no place")
        launched(self.name, place, launch.story, launch.adhoc)
        return place

    def start(self, name, args, launch, say, again, dialog):
        """Opens a place, then waits up to 15 s until Claude Code lists a session of that name
        that runs under what the driver started there and, where the driver shows a screen,
        Remote Control has printed its link, 30 s more for the link alone. A place the driver no
        longer lists within the 15 s, from the third second on and with no session of the name
        listed, means claude exited at start-up: a place is opened once more, once `again` let
        it; a second exit is Refused. A place that is still there at 15 s with nothing listed
        gets the warning, with the first line of the dialog its screen ends on. A dialog is
        never answered here."""
        def under(pid, top, above):
            for _ in range(ABOVE):
                if pid == top:
                    return True
                pid = above.get(pid)
            return False

        link, tried, up, i = None, 1, False, 0
        place = self.make(name, args, launch)
        while i < STARTING:
            i += 1
            time.sleep(1)
            try:
                listed, above = agents(), parents()
            except (OSError, ValueError, subprocess.SubprocessError):
                listed, above = [], {}
            there = self.listed()
            top = (there or {}).get(place)
            up = up or any(isinstance(a, dict) and a.get("name") == name and a.get("pid")
                           and (top is None or under(a["pid"], top, above)) for a in listed)
            link = LINK.search(self.shown(place)) if launch.rc else None
            if up and (link or not launch.rc or "screen" in self.silent):
                break
            if up or there is None or place in there or i < 3:
                continue
            if tried == 2:
                raise Refused("exited", f"session '{name}' exited twice at start-up; {self.program} kept no place for it")
            if again:
                again(name)
            if say:
                say(f"warning: session '{name}' exited at start-up in {place}; opened again")
            tried, i = 2, 0
            place = self.make(name, args, launch)
        if up and launch.rc and not link and "screen" not in self.silent:
            for _ in range(LINKING):
                time.sleep(1)
                link = LINK.search(self.shown(place))
                if link:
                    break
        parked = ""
        if not up:
            parked = dialog(self.shown(place)) if dialog else ""
            if say and parked:
                say(f"warning: session '{name}' is parked at a start-up prompt in {place} ({parked}); answer it there")
            elif say:
                say(f"warning: no session '{name}' came up in {place} within 15s; look at it (a folder trust prompt?)")
        return Opened(name=name, place=place, where=f"driver={self.name} id={place}", up=up, created=1, tried=tried,
                      rc=(link.group(0) if link else "pending") if launch.rc else "off", parked=parked)


TMUX, BACKGROUND = Tmux(), Background()
BACKENDS = {TMUX.name: TMUX, BACKGROUND.name: BACKGROUND}  # the ones built in, by the name `driver=` takes in handoff.conf
OUTSIDE = {}  # the outside drivers asked for so far, by their executable


def configured():
    """The name on handoff.conf's last `driver=` line, tmux without one."""
    found = ""
    try:
        for line in CONF.read_text(errors="replace").splitlines():
            m = re.match(r"\s*driver\s*=\s*(.*)", line)
            if m:
                found = m.group(1).rstrip()
    except OSError:
        pass
    return found or "tmux"


def driver(name=None):
    """The backend of that name, the one handoff.conf names when none is given: a built-in one,
    else an outside driver, whose executable `flowkit-driver-<name>` is looked for on the PATH,
    in ~/.local/bin (where flowkit's own copies sit) and beside flowkit's commands. None when
    there is no such executable."""
    name = name or configured()
    if name in BACKENDS:
        return BACKENDS[name]
    if not NAMED.match(name):
        return None
    program = f"flowkit-driver-{name}"
    own = Path(__file__).resolve().parent.parent / "bin"
    path = shutil.which(program, path=os.pathsep.join([os.environ.get("PATH", ""), str(Path.home() / ".local/bin"), str(own)]))
    if not path:
        return None
    return OUTSIDE.setdefault(path, Driver(name, path))


def terminal():
    """The backend of a session that has a terminal of its own (`interactive` in Claude Code's
    list): the outside driver handoff.conf names, tmux with any other line."""
    at = driver()
    return at if isinstance(at, Driver) else TMUX
