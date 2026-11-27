#!/usr/bin/env python3
"""Files behind the handoff skills: the repo's pick-up doc, the ladder file, the away flag,
saved prompts and story files. The skills call this with plain arguments; nothing here asks.

  handoff.py state <data-dir> <repo>             what a close needs to know, one line each
  handoff.py pickup-doc <data-dir> <repo> <doc>  remember the repo's pick-up doc
  handoff.py save-prompt <name> [--report-to <session>]
                                                 stdin -> ~/.claude/spawned/<name>.md; with a session,
                                                 `Report-to: <session>` goes into the prompt's header; a
                                                 prompt starting with `/` is a command, one line, saved as is
  handoff.py self                                this session's name (what a helper's Report-to line names)
  handoff.py record <story>                      stdin (first line `## <name> · YYYY-MM-DD HH:MM`) appended
                                                 to ~/.claude/stories/<slug>.md (the story slugified as
                                                 flowkit-spawn does), then the reporter is told `closed <name>`
  handoff.py chain-end <story> [<value|words>]   the story file's `Chain end:` line (what the chain's last
                                                 slice leaves behind): print it, or none; with a chain_end=
                                                 value or your own words, write it into the header
  handoff.py wake <slug> <name>                  ring a busy background reporter once it is idle (5 min)
  handoff.py wake --id <session id> <text>       resume a background session with <text> once it is idle
  handoff.py conf <key> <value>                  set a key of ~/.config/flowkit/handoff.conf
  handoff.py settings diff|install <key=json>…   values for the user's settings.json (a.b nests)
  handoff.py away [--by <session>]               the ~/.claude/away flag: in effect 5 minutes from now (its
                                                 first line), for 12 hours; names who set it and when; a
                                                 last call is typed into every idle coordinator now, and
                                                 `away-nudge --wait` starts; a flag already set stays as it is
  handoff.py awake [--by <session>]              the ~/.claude/awake flag, for 12 hours: out but reachable
                                                 from a phone, so the computer stays up between turns and
                                                 questions still reach the user; an away flag is cleared
  handoff.py back [--by <session>]               clear both flags, then list the sessions waiting on the user
  handoff.py away-status                         away: since <stamp> (in effect), from <stamp> (set, not
                                                 yet in effect) or off
  handoff.py away-nudge [--wait]                 once the flag is in effect, nudge the sessions parked on a
                                                 question (bin/flowkit-away), once per flag; one the user
                                                 typed to in the last 5 minutes is left for a later pass.
                                                 --wait, what `away` starts: from the minute the flag takes
                                                 effect, then once a minute while a session was left
  handoff.py restamp                             an unattended close: the flag's 12 hours start again
                                                 while it exists (no nudge, no usage), so a chain
                                                 longer than that stays unattended
  handoff.py question-gate                       PreToolUse hook on AskUserQuestion: denied while the flag
                                                 is in effect, unless the user typed to this session in
                                                 the last 5 minutes
  handoff.py context                             UserPromptSubmit, PostToolBatch and PostToolUse (on
                                                 AskUserQuestion) hook: the context line, the away flag's
                                                 set and clear once each, the stamp of a prompt the user
                                                 typed or a question they answered; with idle_release=on
                                                 a prompt typed at the computer clears the away flag
  handoff.py api-error | api-ok                  StopFailure and Stop hooks: the retry record and line, and
                                                 the waiter (retry-wait <session id>): after a usage limit
                                                 unless limitresume=off, after an API error with autoretry=on
  handoff.py session locate|read|type|claim|release …
                                                 bin/flowkit-session: where a session runs, what it shows, a
                                                 line typed into it, and a usage-limit stop taken over from
                                                 the waiter, for a hook outside flowkit (`session --help`)
  handoff.py reconnect [--all | <session id>]    the tick's and the Stop hook's, after the account of a login
                                                 changed: /remote-control typed into each session that lost
                                                 its link to it, once it is idle with a free prompt box
                                                 (--all: every session is looked at, not only those owed)
  handoff.py remind [--dry-run]                  the tick's, with remind=on: what waits on the user (a pane
                                                 parked at a start-up prompt, a usage-limit stop flowkit
                                                 cannot continue, the sessions and test items waiting on
                                                 them) into flowkit/remind/<stamp>.md, pushed through the
                                                 oldest idle reporter, else notify=, else a macOS
                                                 notification; --dry-run says what it would say now and
                                                 why it would stay silent, sends nothing, keeps no state
  handoff.py usage [--json]                      the plan's usage left (bin/flowkit-usage)
  handoff.py statusline diff|install <root>      the status line that records context and cost
  handoff.py tmux diff|install <root>            the tmux kit and the line that sources it
  handoff.py timer diff|install <root>           the timer that runs the tick every 15 seconds, with tmux
                                                 attached or not (bin/flowkit-tick): a LaunchAgent, so
                                                 macOS only; elsewhere the tmux status bar runs the tick
  handoff.py setup remove [--dry-run]            take away what the setups put on this computer outside the
                                                 plugin: the timer, the commands in ~/.local/bin, the tmux
                                                 kit and its line; the settings files stay
  handoff.py refresh <root>                      update an installed status line, tmux kit and the commands
                                                 the timer runs, prune the prompt copies and the sessions'
                                                 hook state older than 30 days (SessionStart)
"""
import contextlib
import difflib
import fcntl
import importlib.util
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

HOME = Path.home()
CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
AWAY = CLAUDE / "away"
AWAY_HOURS = 12
AWAY_DELAY = 5  # minutes from `away` to the flag taking effect: the coordinators' last call
AWAY_NUDGED = CLAUDE / "flowkit" / "away-nudged"  # the flag the nudge ran for, nobody left for a later pass
AWAY_CLEARED = CLAUDE / "flowkit" / "away-cleared"  # who cleared the flag last, when, and which flag it was
PRESENT = 5  # minutes after the user typed to a session (or answered its question) in which it may still ask
RELEASE_GRACE = 30  # idle_release=on: minutes a flag is in effect before a typed prompt may clear it (the goodnight prompts)
RELEASE_IDLE = 120  # and the seconds since the last key or mouse input within which that prompt was typed at the computer
COORDINATOR = re.compile(r"^Coordinator: (\S+)", re.M)  # a coordination board names its coordinator
COORD_NAME = re.compile(r"(?:^|-)coord")  # coord-1, story-coord, story-coordinator
SPAWNED = CLAUDE / "spawned"
PRUNE_DAYS = 30
COPY = re.compile(r"-\d{8}-\d{6}(-shared)?\.md$")  # flowkit-spawn's stamped copies, never a saved <name>.md
STORIES = CLAUDE / "stories"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "flowkit"
LADDER = CONFIG / "ladder.md"
SETTINGS_FILE = CONFIG / "handoff.conf"
STATUSLINE = CONFIG / "statusline.py"
SETTINGS = CLAUDE / "settings.json"
SHIMS = {  # the commands a tmux.conf and the timer call: copies with no version in their path, the tick first
    "bin/flowkit-tick": HOME / ".local" / "bin" / "flowkit-tick",
    "bin/flowkit-tmux": HOME / ".local" / "bin" / "flowkit-tmux",
    "bin/flowkit-away": HOME / ".local" / "bin" / "flowkit-away",
    "bin/flowkit-usage": HOME / ".local" / "bin" / "flowkit-usage",
    "bin/flowkit-session": HOME / ".local" / "bin" / "flowkit-session",
}
KIT = {  # the tmux kit: where each file of the plugin is copied, so a tmux.conf never points at a version
    **SHIMS,
    "tmux/capacity-shim.py": HOME / ".local" / "scripts" / "capacity.py",  # where a 0.8.0 tick looks
    "tmux/tmux.conf": CONFIG / "tmux.conf",
    "tmux/cheatsheet.md": CONFIG / "tmux-cheatsheet.md",
}
KIT_CONF = KIT["tmux/tmux.conf"]
KIT_STAMP = CONFIG / "kit-version"  # the plugin version the kit was copied from; refresh never goes back
KIT_NOTE = "# flowkit tmux kit (handoff plugin); your own settings go below this line"
TIMER = "flowkit.tick"  # the timer's launchd label
TIMER_PLIST = HOME / "Library" / "LaunchAgents" / f"{TIMER}.plist"
TIMER_EVERY = 15  # seconds between two passes of the tick
TIMER_ENV = ("CLAUDE_CONFIG_DIR", "XDG_CONFIG_HOME", "LANG")  # carried into the timer when the setup's shell has them
AWAY_BIN = Path(__file__).resolve().parent.parent / "bin" / "flowkit-away"
USAGE_BIN = Path(__file__).resolve().parent.parent / "bin" / "flowkit-usage"
STATS = CLAUDE / "session-stats"
CTX_STEPS = (40, 50, 60)
CTX_CLEAR = 35  # a reading this low (a /compact, a fresh start) lets the steps fire again
CTX_WARM = 30  # requests before a session hears anything: a successor that starts high must not loop
AWAKE = CLAUDE / "awake"  # out but reachable: `awake` writes it (a setup of the user's own may), counted from its write
RETRY = CLAUDE / "flowkit" / "retry"  # <session id>.json while a session sits on an API error
RETRIES_LOG = CLAUDE / "flowkit" / "retries.log"  # retry lines of sessions outside a story
RETRY_KINDS = ("server_error", "overloaded")  # what the API-error waiter continues; rate_limit has limit_wait
RETRY_STEPS = (2, 5, 10, 20, 30)  # minutes the waiter waits before each continue, then every 30
RETRY_HOURS = 12
SETTLE = 10  # seconds after the error before the transcript is read as the session's resting state
LIMIT_GRACE = 3  # minutes after a usage limit's reset before the session is continued
LIMIT_HOLD = 30  # minutes before that from which the computer is kept awake
LIMIT_CONTINUES = 3  # continues that ended on the limit again, no clean turn end in between: then it waits for the user
NATIVE_HOURS = 24  # a reset this near is Claude Code's own to wait for, in a session without Remote Control
SWITCH_QUIET = 1  # minutes after an account change in which a stop on the limit says nothing of the new account
NOTICE = ("Remote Control disconnected", "account or organization changed")  # what a session logs when its login changed
REMOTE = CLAUDE / "flowkit" / "remote"  # <session id>.owed while a session is owed its Remote Control, .json: what came of it
REMOTE_WAIT = 20  # seconds a typed /remote-control has to bring the session's link back
ACCOUNTS = {}  # account file → (what stat said, who it named then): parsed again only once it was rewritten
TAIL = 2 * 1024 * 1024  # bytes of a transcript's end read for its last limit line and what follows it
LOCAL = ("<command-", "<local-command-", "<bash-")  # how the lines of a /command or a ! shell line start: no turn
WRAPPERS = ("<task-notification", "Another Claude session sent a message", "<cross-session-message",
            "[Cross-session") + LOCAL  # prompts nobody typed: a task's notice, another session's message
FLOWKIT_LINE = re.compile(r"Your last turn (?:stopped on the usage limit|ended on an API error|hit the output token limit)"
                          r"|away(?: since .+?)?: I am off\.|report now$|closed [\w.-]+$|last call: |reminder: |/remote-control$"
                          r"|wake: ")  # lines flowkit types, coordinate's wake of a stopped worker among them
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
RESETS = re.compile(r"resets (?:at )?(?:(?P<mon>[A-Za-z]{3}) (?P<day>\d{1,2}),? (?:at )?)?(?P<h>\d{1,2})(?::(?P<m>\d\d))?"
                    r"(?: ?(?P<ap>[ap]m))?(?: \((?P<tz>[^)\s]+)\))?", re.I)
REMIND = CLAUDE / "flowkit" / "remind.json"  # what the reminders have said: {once: {key: when}, digest: {at, keys}},
#                                             and the last one nothing carried: failed: {at, lines}
REMIND_IDLE = 10  # minutes without keyboard or mouse input after which the user is not at the computer
REMIND_EVERY = 120  # minutes between two digests
REMIND_DIR = CLAUDE / "flowkit" / "remind"  # each reminder's text as <stamp>.md; remind-latest.md beside the folder
REMIND_QUIET = 70  # seconds a rung reporter waits before its push: Claude Code skips one sent within a minute of a typed line
REMIND_WAIT = 150  # seconds a rung reporter has to show its push in its transcript before the next one is rung
REMIND_RETRY = 10  # minutes before a reminder that nothing carried is tried again
REMIND_RING = ("reminder: from flowkit's tick, not the user. Claude Code skips a push for a minute after a line is typed "
               "into a session, this one included, so first run `sleep {quiet}` with the Bash tool, run_in_background "
               "true, and end the turn; when its completion notice arrives, send the user one push notification with the "
               "PushNotification tool (load it through ToolSearch if it is not loaded), its message word for word "
               'everything after "Message: "; then end the turn with "reminder sent" and nothing else. The full '
               "list: {path}. Message: {push}")
PARKED = CLAUDE / "flowkit" / "parked.json"  # flowkit-tmux's: the panes parked at a start-up prompt
PARKED_FRESH = 300  # seconds after which that file says nothing: the tick that writes it has stopped
HEADING = "WHERE TO PICK UP"
NAMES = ("PLAN.md", "BUILD-PLAN.md", "HANDOFF.md", "ROADMAP.md", "docs/PLAN.md", "docs/plan.md", "docs/HANDOFF.md")
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CHAIN_END = "mr-ready"  # chain_end= of handoff.conf when the key is absent
CHAIN_ENDS = {  # a chain's end: what its slices do about pushing, the MR and the merge; the last slice ends it
    "branch": "no slice pushes; the last slice leaves the branch committed and the user pushes; no watch",
    "draft-mr": "each slice pushes its green commits, the first push opens a Draft MR; the last slice pushes, "
                "registers the watch and closes: the MR stays in Draft, a red pipeline gets one fix slice",
    "mr-ready": "each slice pushes its green commits, the first push opens a Draft MR; the last slice pushes, "
                "registers the watch and closes: the watch takes the MR out of Draft on green, a red pipeline gets "
                "one fix slice, no merge",
    "merge": "each slice pushes its green commits, the first push opens a Draft MR; the last slice takes it out of "
             "Draft, sets auto-merge, registers the watch and closes: the watch reports the merge and the target "
             "branch's pipeline",
}


def _backend():
    """backend.py beside this file: where a session runs and how a line reaches it there."""
    spec = importlib.util.spec_from_file_location("flowkit_backend", Path(__file__).resolve().parent / "backend.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backend = _backend()
# TMUX: where a session with a terminal of its own runs, tmux unless handoff.conf names an outside driver
TMUX, BACKGROUND = backend.terminal(), backend.BACKGROUND
parked, draft = TMUX.parked, TMUX.draft


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def safe(name, what):
    if not SAFE.match(name):
        fail(f"{what} must be letters, digits, '.', '_' or '-': {name!r}")
    return name


def label(name):
    """A session's name as the flag keeps it: a hand-started session carries its title, spaces and
    all, so what `safe` would refuse becomes `-`; nothing usable left: `unknown`."""
    return re.sub(r"[^A-Za-z0-9._-]", "-", name).lstrip("._-") or "unknown"


def slugify(text):
    """A story's slug, as flowkit-spawn makes it for the tab and `<slug>-report`."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).lstrip("-")[:32].rstrip("-")


def repo_root(repo):
    out = subprocess.run(["git", "-C", repo, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if out.returncode:
        fail(f"not a git repository: {repo}")
    return out.stdout.strip()


def docs_file(data):
    return Path(data) / "pickup-docs.json"


def load_docs(data):
    try:
        return json.loads(docs_file(data).read_text())
    except (OSError, ValueError):
        return {}


def candidates(root):
    found = []
    tracked = subprocess.run(["git", "-C", root, "ls-files", "*.md"], capture_output=True, text=True).stdout.split()
    for rel in tracked:
        try:
            if HEADING in (Path(root) / rel).read_text(errors="replace"):
                found.append(rel)
        except OSError:
            pass
    for rel in NAMES:
        if (Path(root) / rel).is_file() and rel not in found:
            found.append(rel)
    return found


def setting(key, default):
    """A key=value line of ~/.config/flowkit/handoff.conf, the plugin's own settings."""
    try:
        for line in SETTINGS_FILE.read_text().splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == key:
                default = v.strip()
    except OSError:
        pass
    return default


def away_flag():
    """The away flag while it counts, as {text, stamp, at, by, set}, else None. Its first line is the
    stamp it takes effect (`at`; one that does not parse: when the file was written), then `by:` who
    set it and `set:` when; a flag written raw, its stamp alone, is in effect from that stamp, set by
    `unknown`. It counts until AWAY_HOURS after its stamp or its last re-stamp, whichever is later."""
    try:
        text, mtime = AWAY.read_text(), AWAY.stat().st_mtime
    except OSError:
        return None
    lines = text.splitlines() or [""]
    stamp = lines[0].strip()
    try:
        at = datetime.strptime(stamp, "%Y-%m-%d %H:%M").timestamp()
    except ValueError:
        at = mtime
    if time.time() > max(at, mtime) + AWAY_HOURS * 3600:
        return None
    fields = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
    return {"text": text, "stamp": stamp or "set", "at": at, "by": fields.get("by", "unknown"),
            "set": fields.get("set") or time.strftime("%Y-%m-%d %H:%M", time.localtime(min(at, mtime)))}


def away_since():
    """The away flag's stamp while it is in effect, else None."""
    flag = away_flag()
    return flag["stamp"] if flag and flag["at"] <= time.time() else None


def away_status():
    flag = away_flag()
    if not flag:
        return "away: off"
    if flag["at"] <= time.time():
        return f"away: since {flag['stamp']}"
    return f"away: from {flag['stamp']} (set by {flag['by']} at {flag['set']})"


def away(by):
    """Set the away flag: in effect AWAY_DELAY minutes from now, naming who set it and when. The idle
    coordinators get a last call now; the sessions parked on a question get the nudge once it takes
    effect, from a process of its own started here (`away-nudge --wait`), so it needs no tmux tick. A
    flag already set stays as it is, and nudges again once in effect."""
    flag = away_flag()
    if flag:
        print(f"away: {'since' if flag['at'] <= time.time() else 'from'} {flag['stamp']} (already set by "
              f"{flag['by']} at {flag['set']}; clears with back or after {AWAY_HOURS} hours)")
    else:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime((int(time.time()) + AWAY_DELAY * 60 + 59) // 60 * 60))
        CLAUDE.mkdir(parents=True, exist_ok=True)
        fresh = CLAUDE / ".away.tmp"
        fresh.write_text(f"{stamp}\nby: {by}\nset: {time.strftime('%Y-%m-%d %H:%M')}\n")
        os.replace(fresh, AWAY)  # whole, so a reader never takes half a stamp for a raw flag in effect now
        print(f"away: from {stamp} (set by {by}; questions go through until then; clears with back or after "
              f"{AWAY_HOURS} hours)")
    sys.stdout.flush()
    usage()
    if not flag:
        last_call(by, stamp)
        subprocess.Popen([sys.executable, __file__, "away-nudge", "--wait"], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif flag["at"] <= time.time():
        reach("nudge")


def clear_away(by):
    """Remove the away flag, noting who and when (the context hook tells every session). True when
    one was there."""
    try:
        text = AWAY.read_text()
        AWAY.unlink()
        AWAY_CLEARED.parent.mkdir(parents=True, exist_ok=True)
        AWAY_CLEARED.write_text(json.dumps({"by": by, "at": time.strftime("%Y-%m-%d %H:%M"), "flag": text}))
    except OSError:
        return False
    return True


def awake(by):
    """Set the awake flag: out, but reachable from a phone. The computer is kept up between turns and
    `autoretry` counts it, as while away; questions still reach the user and a max launch still asks,
    so an away flag is cleared. It counts AWAY_HOURS from its write, or until `back`."""
    was_away = clear_away(by)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    CLAUDE.mkdir(parents=True, exist_ok=True)
    AWAKE.write_text(f"{stamp}\nby: {by}\n")
    print(f"awake: since {stamp} (set by {by}; the computer stays up between turns, questions still reach you; "
          f"clears with back or after {AWAY_HOURS} hours)")
    if was_away:
        print("away: off")


def back(by):
    """Clear the away and the awake flag, then list the sessions waiting on the user."""
    clear_away(by)
    print("away: off")
    try:
        AWAKE.unlink()
        print("awake: off")
    except OSError:
        pass
    sys.stdout.flush()
    reach("waiting")


def last_call(by, stamp):
    """`away`'s last call: one line typed into every idle coordinator in a tmux pane (a name on a
    coordination board's `Coordinator:` line, or with `coord` at its start or after a `-`), only into
    a free prompt box; never into a reporter, the setter, or a session that is busy or waits."""
    try:
        rows = [r for r in backend.agents() if isinstance(r, dict)]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        print("last call: cannot read `claude agents --json`: no coordinator told")
        return
    boards = set()
    for path in (CLAUDE / "coordination").glob("*.md"):
        try:
            boards.update(m.strip("`") for m in COORDINATOR.findall(path.read_text(errors="replace")))
        except OSError:
            pass
    text = (f"last call: the user goes away at {stamp[-5:]}. Ask now anything you need from them; from then on "
            'questions are denied and you take the recommended option, marked "(auto)".')
    for row in rows:
        name = str(row.get("name") or "")
        if label(name) == by or name.endswith("-report") or not (name in boards or COORD_NAME.search(name)):
            continue
        if backend.mode(row) != "idle":
            print(f"no last call {name}: {backend.mode(row)}")
            continue
        pane, why = type_into(row, text)
        print(f"no last call {name}: {why}" if why else f"last call {name} (tmux {pane})")


def nudge_pass():
    """Nudge the sessions parked on a question (bin/flowkit-away), but not one the user typed to, or
    whose question they answered, in the last PRESENT minutes: they may be answering it now. True
    when nobody was left for a later pass. With `claude agents` unreadable everybody is nudged."""
    try:
        rows = [r for r in backend.agents() if isinstance(r, dict) and r.get("status") == "waiting"]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        rows = []
    name = lambda r: str(r.get("name") or "?")
    later = {name(r) for r in rows if r.get("waitingFor") == "input needed" and not name(r).endswith("-report")
             and SAFE.match(str(r.get("sessionId") or "")) and present(r["sessionId"])}
    if not later:
        reach("nudge")
        return True
    for who in sorted(later):
        print(f"present {who}: the user typed to it in the last {PRESENT} minutes; nudged once those are over")
    only = [name(r) for r in rows if name(r) not in later]  # by name: a namesake of one left waits with it
    sys.stdout.flush()
    if only:
        reach("nudge", *(arg for who in only for arg in ("--only", who)))
    return False


def nudge_round(text, flag):
    """One pass for the flag in effect, one at a time; True once the flag needs none: it ran with
    nobody left for later (AWAY_NUDGED says so from then on), or the flag has expired."""
    with AWAY_NUDGED.with_suffix(".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False  # another pass is at it
        try:
            done = AWAY_NUDGED.read_text() == text
        except OSError:
            done = False
        if not done and (not flag or nudge_pass()):
            AWAY_NUDGED.write_text(text)  # after the nudge returned: one that died is tried again
            done = True
        return done


def away_nudge(wait=False):
    """The nudge of the flag once it is in effect, once per flag: one pass now, or with `wait` (the
    process `away` starts) from the minute the flag takes effect, then once a minute while a pass
    left a session for later. It follows the flag that is there and ends with it."""
    unit = float(os.environ.get("FLOWKIT_RETRY_MINUTE") or 60)
    AWAY_NUDGED.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            text = AWAY.read_text()
        except OSError:
            return
        flag = away_flag()
        ahead = flag["at"] - time.time() if flag else 0
        if (ahead <= 0 and nudge_round(text, flag)) or not wait:
            return
        time.sleep(min(ahead, unit / 2) if ahead > 0 else unit)


def mark_file(sid):
    """A session's hook state: the context steps, whether it had a prompt, when the user last typed
    to it, the away flag it last heard of. Under flowkit/context/, where a hook, the tick and a
    timer all find it whatever temp folder each was given."""
    return CLAUDE / "flowkit" / "context" / f"{sid}.json"


def mark_read(sid):
    """What a session's mark holds, {} without one. A session that started before 0.14.0 keeps its
    mark in the temp folder until it is resumed: read there when this one is missing."""
    for path in (mark_file(sid), Path(tempfile.gettempdir()) / f"flowkit-context-{sid}.json"):
        try:
            seen = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(seen, dict):
            return seen
    return {}


def mark_write(sid, seen):
    mark = mark_file(sid)
    mark.parent.mkdir(parents=True, exist_ok=True)
    mark.write_text(json.dumps(seen))


def typed_by_user(prompt, first):
    """A prompt the user typed, at the computer or from the phone: not the session's first (a
    launch's brief), not a wrapper (a task's notice, a /command's lines, another session's message)
    and not a line flowkit types (a coordinator's `wake: ` of a stopped worker is one)."""
    prompt = prompt.lstrip()
    return not first and not prompt.startswith(WRAPPERS) and not FLOWKIT_LINE.match(prompt)


def present(sid):
    """The user typed to this session, or answered its question, in the last PRESENT minutes."""
    try:
        return time.time() - mark_read(sid).get("typed", 0) < PRESENT * 60
    except TypeError:
        return False


def idle_release():
    """idle_release=on in handoff.conf: a prompt the user typed at the computer clears an away flag
    that has been in effect for RELEASE_GRACE minutes, so a flag left behind by a day out does not
    silence the sessions all evening. At the computer means a key or the mouse was used in the last
    RELEASE_IDLE seconds: a prompt sent from a phone clears nothing, and where the system has no
    such reading (anything but macOS) nothing is cleared."""
    flag = away_flag()
    if not flag or time.time() - flag["at"] < RELEASE_GRACE * 60 or setting("idle_release", "off") != "on":
        return
    idle = hid_idle()
    if idle is not None and idle < RELEASE_IDLE:
        clear_away("a prompt typed at the computer")


def reach(*args):
    """Run bin/flowkit-away with its output going straight to the skill; a miss never fails
    the flag (the sessions are reached as a courtesy, the flag is the record)."""
    try:
        subprocess.run([sys.executable, str(AWAY_BIN), *args], timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"flowkit-away: {e}")


def question_gate():
    """PreToolUse on AskUserQuestion. While the away flag is in effect the question is denied, with the
    reason the session acts on, unless the user typed to this session in the last PRESENT minutes;
    otherwise silent, and the question goes to the user."""
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    since = away_since()
    sid = str(data.get("session_id") or "") if isinstance(data, dict) else ""
    if not since or (SAFE.match(sid) and present(sid)):
        return
    reason = (f"The user is away (since {since}, {AWAY}) and has typed nothing to this session in the last "
              f"{PRESENT} minutes: questions are not asked. Take the recommended "
              'option, mark it "(auto)" in the decision log and in the close, carry on, and never end the turn '
              'on a question. A brief that forbids "(auto)" decisions stops here and lists its questions instead.')
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


def closing(data):
    """A /handoff:close typed as the prompt, or run as a skill in this batch of tool calls."""
    prompt = str(data.get("prompt") or "").strip()
    if prompt == "/handoff:close" or prompt.startswith("/handoff:close "):
        return True
    return any(c.get("tool_name") == "Skill" and (c.get("tool_input") or {}).get("skill") == "handoff:close"
               for c in data.get("tool_calls") or [] if isinstance(c, dict))


def warm(transcript):
    try:
        text = Path(transcript).read_text(errors="replace")
    except (OSError, TypeError):
        return False
    return len(set(re.findall(r'"requestId":"(req_[^"]*)"', text))) >= CTX_WARM


def away_news(seen):
    """One line when the away flag was set or cleared since this session last heard (`seen["away"]`,
    the flag's text), else None."""
    flag = away_flag()
    was, now = seen.get("away", ""), flag["text"] if flag else ""
    if now == was:
        return None
    seen["away"] = now
    if flag and flag["at"] > time.time():
        return (f"Away flag set by {flag['by']} at {flag['set']}: the user is away from {flag['stamp']}. Until "
                f"then questions go through; from then on a question is denied unless the user typed to this "
                f'session in the {PRESENT} minutes before it, and you take the recommended option, marked "(auto)".')
    if flag:
        return (f"Away flag set by {flag['by']} at {flag['set']}, in effect since {flag['stamp']}: a question is "
                f"denied unless the user typed to this session in the last {PRESENT} minutes; take the "
                'recommended option, marked "(auto)".')
    if AWAY.exists():
        return f"Away flag expired ({AWAY_HOURS} hours after it took effect): questions go to the user again."
    try:
        cleared = json.loads(AWAY_CLEARED.read_text())
    except (OSError, ValueError):
        cleared = {}
    if isinstance(cleared, dict) and cleared.get("flag") == was:
        return f"Away flag cleared by {cleared.get('by')} at {cleared.get('at')}: the user is back, questions go to them again."
    return "Away flag removed: the user is back, questions go to them again."


def context_hook():
    """UserPromptSubmit, PostToolBatch, and PostToolUse on AskUserQuestion. A session cannot see its
    own context use; the status line writes it to session-stats/<id>.json, one message behind. Every
    prompt at 40% or more gets one line; mid-run, each of 40, 50 and 60% is told once, and nothing
    from a /handoff:close until the next prompt. Silent for subagents, reporters (*-report), a
    session's first CTX_WARM requests, and without a reading. Also: a prompt the user typed, or an
    answered question, is stamped (the question gate reads it), and the away flag's set and clear
    are told once each."""
    data = json.load(sys.stdin)
    sid = str(data.get("session_id") or "")
    if data.get("agent_id") or not SAFE.match(sid):
        return
    prompt = data.get("hook_event_name") == "UserPromptSubmit"
    seen = mark_read(sid)
    told = dict(seen)
    if data.get("hook_event_name") == "PostToolUse":  # an answered question: the user is there
        mark_write(sid, {**seen, "typed": time.time()})
        return
    if prompt:
        seen.pop("quiet", None)
        if typed_by_user(str(data.get("prompt") or ""), not seen.get("prompted")):
            seen["typed"] = time.time()
            idle_release()
        seen["prompted"] = True
    news = away_news(seen)
    if closing(data):
        seen["quiet"] = True
    try:
        stats = json.loads((STATS / f"{sid}.json").read_text())
    except (OSError, ValueError):
        stats = {}
    pct, line = stats.get("ctx_pct"), None
    if isinstance(pct, (int, float)) and not str(stats.get("session_name") or "").endswith("-report"):
        pct = int(pct)
        if 0 <= pct <= CTX_CLEAR:
            seen.pop("step", None)
        step = max((s for s in CTX_STEPS if pct >= s), default=0)
        if step and not seen.get("quiet") and (prompt or step > seen.get("step", 0)) and \
                (seen.get("warm") or warm(data.get("transcript_path"))):
            seen.update(warm=True, step=max(step, seen.get("step", 0)))
            line = (f"Context ~{pct}%. Repo work: /handoff:close at a stopping point. A hub or coordinator: "
                    "a paste-ready `/compact Keep: …` line under FOR ME.")
    if seen != told:
        mark_write(sid, seen)
    if news or line:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit" if prompt else "PostToolBatch",
            "additionalContext": "\n".join(x for x in (news, line) if x)}}))


def usage(*args):
    """bin/flowkit-usage, its line going straight to the caller; its exit code, or 1 when it could not run."""
    try:
        return subprocess.run([sys.executable, str(USAGE_BIN), *args], timeout=60).returncode
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"flowkit-usage: {e}")
        return 1


def state(data, repo):
    root = repo_root(repo)
    doc = load_docs(data).get(root)
    if doc and not (Path(root) / doc).is_file():
        doc = None
    print(f"repo: {root}")
    if doc:
        print(f"pickup-doc: {doc}")
    else:
        found = candidates(root)
        print("pickup-doc: unknown")
        print("pickup-candidates: " + (", ".join(found) if found else "none"))
    print(f"ladder: {LADDER if LADDER.is_file() else 'none'}")
    print(away_status())
    print(f"spawn-driver: {setting('driver', 'tmux')}")
    print(f"chain-end: {chain_end_conf()}")
    print(f"report-to: {setting('report_to', 'off')}")
    print(f"spawned-dir: {SPAWNED}")
    print(f"stories-dir: {STORIES}")


def pickup_doc(data, repo, doc):
    root = repo_root(repo)
    rel = os.path.relpath(Path(root, doc).resolve(), root)
    if rel.startswith(".."):
        fail(f"the pick-up doc must be inside the repo: {doc}")
    docs = load_docs(data)
    docs[root] = rel
    Path(data).mkdir(parents=True, exist_ok=True)
    docs_file(data).write_text(json.dumps(docs, indent=2) + "\n")
    print(f"pickup-doc: {rel} (remembered for {root})")


def save_prompt(name, report_to=None):
    SPAWNED.mkdir(parents=True, exist_ok=True)
    path = SPAWNED / f"{safe(name, 'the session name')}.md"
    text = sys.stdin.read().rstrip("\n") + "\n"
    if text.startswith("/"):
        # Command-first: Claude Code runs the prompt as the user's own command and hands everything
        # after its name to the command as arguments, so the brief lives in the pick-up doc instead.
        if text.count("\n") > 1:
            fail("a prompt starting with / is the command line alone; put the brief in the pick-up block")
        if report_to:
            print(f"command prompt: put Report-to: {report_to} into the brief in the pick-up block", file=sys.stderr)
        report_to = None
    path.write_text(with_report_to(text, report_to) if report_to else text)
    print(path)


def with_report_to(text, session):
    """The prompt with `Report-to: <session>` in its header: on the `Repo:` line of a brief, else as a
    first line of its own. A header that already names one, or a coordinator's REPORTING line
    anywhere, leaves the text as it is."""
    header = text.split("\n\n", 1)[0]
    if re.search(r"(^|\s)Report-to:", header) or re.search(r"^REPORTING\b", text, re.M):
        return text
    first, sep, rest = text.partition("\n")
    if first.startswith("Repo:"):
        return f"{first}   Report-to: {session}{sep}{rest}"
    return f"Report-to: {session}\n\n{text}"


def whoami():
    """This session's name, id and session id: `~/.claude/sessions/$CLAUDE_PID.json` (claude writes it
    at start-up, so a live pid's file is its own), else the `claude agents --json` row of the nearest
    parent pid (the Bash tool's shell is a child of the claude process, maybe nested)."""
    info = session_file(os.environ.get("CLAUDE_PID", ""))
    if not info.get("name"):
        try:
            rows = backend.agents()
        except (OSError, subprocess.TimeoutExpired, ValueError):
            rows = []
        by_pid = {r["pid"]: r for r in rows if isinstance(r, dict) and r.get("pid")}
        ps = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=10).stdout
        parents = {int(a): int(b) for a, b in (row.split() for row in ps.splitlines() if len(row.split()) == 2)}
        pid = os.getppid()
        for _ in range(8):
            if pid in by_pid:
                info = by_pid[pid]
                break
            pid = parents.get(pid, 0)
            if pid <= 1:
                break
    if not info.get("name"):
        print("this session is not listed by `claude agents`; name it with /rename and retry", file=sys.stderr)
        sys.exit(4)
    print(f"name: {info['name']}")
    print(f"id: {info.get('id') or '-'}")
    print(f"session: {info.get('sessionId') or '-'}")


def reachable():
    """Away, or the awake flag (out but reachable from a phone) younger than AWAY_HOURS. Away counts
    from the moment the flag is set, the minutes before it takes effect included: an overnight close
    launches within them, and an error that ends a turn then must still find its waiter."""
    try:
        awake = time.time() - AWAKE.stat().st_mtime <= AWAY_HOURS * 3600
    except OSError:
        awake = False
    return bool(away_flag()) or awake


def account_file(config):
    """The file Claude Code keeps a login in, under an environment whose CLAUDE_CONFIG_DIR is `config`
    (None or "": unset): <config>/.claude.json, else ~/.claude.json. Two logins share one config
    folder when one runs with the variable unset and the other with it set to ~/.claude, so the
    folder (CLAUDE above) never tells them apart; this file does."""
    return Path(config or HOME) / ".claude.json"


def identity(config):
    """Who is logged in under that environment: the account and organization ids of its account
    file, or None when it names none (no login, an API key) or cannot be read."""
    path = account_file(config)
    try:
        st = path.stat()
        seen = (st.st_mtime_ns, st.st_size, st.st_ino)
        if ACCOUNTS.get(path, (None,))[0] != seen:
            who = json.loads(path.read_text()).get("oauthAccount")
            ACCOUNTS[path] = (seen, {"account": who["accountUuid"], "organization": who.get("organizationUuid")}
                              if isinstance(who, dict) and who.get("accountUuid") else None)
        return ACCOUNTS[path][1]
    except (OSError, ValueError, AttributeError):
        return None


def login_env(rec):
    """This process's environment with the login of a limit record's session: CLAUDE_CONFIG_DIR as
    it was at the stop, unset when it was unset. A waiter the tick starts has the tmux server's
    environment, whose login may be the other one."""
    env = dict(os.environ)
    if "config" in rec:
        env.pop("CLAUDE_CONFIG_DIR", None)
        if rec["config"] is not None:
            env["CLAUDE_CONFIG_DIR"] = rec["config"]
    return env


def session_file(pid, sid=None):
    """~/.claude/sessions/<pid>.json, with a sid only while it is still that session's: pids are
    reused."""
    try:
        data = json.loads((CLAUDE / "sessions" / f"{pid}.json").read_text())
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) and (sid is None or data.get("sessionId") == sid) else {}


def retry_record(sid):
    try:
        return json.loads((RETRY / f"{sid}.json").read_text())
    except (OSError, ValueError):
        return None


@contextlib.contextmanager
def retry_lock():
    """One writer of the retry records at a time: the hooks and a limit waiter all change them."""
    RETRY.mkdir(parents=True, exist_ok=True)
    with (RETRY / ".mutex").open("w") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def retry_update(sid, change):
    """Read a session's retry record, let `change` edit it (it gets None when there is none, and
    returns None to write nothing) and put it back whole, so a reader never sees half of one."""
    with retry_lock():
        rec = change(retry_record(sid))
        if rec is not None:
            fresh = RETRY / f".{sid}.tmp"
            fresh.write_text(json.dumps(rec))
            os.replace(fresh, RETRY / f"{sid}.json")
        return rec


def retry_set(sid, **fields):
    """Set fields of a session's retry record, if it still has one."""
    return retry_update(sid, lambda rec: rec and {**rec, **fields})


def once(rec, key):
    """True the first time `key` is asked of a record: one story line per record and kind."""
    said = rec.setdefault("lines", [])
    if key in said:
        return False
    said.append(key)
    return True


def retry_log(rec, text):
    """One line into the story file of the session's tab, or into retries.log outside a story."""
    path = STORIES / f"{rec['story']}.md" if rec.get("story") else None
    with (path if path and path.is_file() else RETRIES_LOG).open("a") as f:
        f.write(text + "\n")


def auto_continue():
    """autoContinueAtUsageLimit of the user's settings: True, False, or None when they do not say."""
    try:
        value = json.loads(SETTINGS.read_text()).get("autoContinueAtUsageLimit")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, bool) else None


def limit_resume():
    """A session stopped on a usage limit is continued after the reset, the user away or not, unless
    their settings say autoContinueAtUsageLimit: false or handoff.conf says limitresume=off."""
    return setting("limitresume", "on") != "off" and auto_continue() is not False


def api_error_hook():
    """StopFailure: an API error ended the turn, so Stop (and its idle hold) did not fire. Writes
    the retry record that labels the tab `error` (`limit` for a usage limit), logs one line, and
    starts the waiter that continues the session: after a usage limit unless limit resume is off,
    after an API error with autoretry=on while the user is away or awake."""
    data = json.load(sys.stdin)
    sid = str(data.get("session_id") or "")
    if data.get("agent_id") or not SAFE.match(sid):
        return
    kind = str(data.get("error") or "unknown")
    pid = os.environ.get("CLAUDE_PID", "")
    now = time.time()
    info = session_file(pid, sid)
    story, screen = "", []
    try:
        here = TMUX.here()
        if here:
            story = TMUX.story(here)
            if kind == "rate_limit":
                screen = TMUX.bottom(here)
    except (OSError, subprocess.TimeoutExpired):
        pass
    fresh = [True]

    def change(rec):
        rec = rec or {"first": now, "attempts": 0}
        resumed = rec.get("pid") != (int(pid) if pid.isdigit() else None)  # the first stop, or of a new process
        rec.update(session=sid, kind=kind, at=now, pid=int(pid) if pid.isdigit() else None,
                   name=info.get("name") or sid[:8], background=info.get("kind") == "background",
                   transcript=str(data.get("transcript_path") or ""), story=story if SAFE.match(story) else "")
        if kind == "rate_limit":  # what the session looked like as it stopped, and one line however often it is woken
            rec.update(remote=bool(info.get("bridgeSessionId")), resume=limit_resume(),
                       status=info.get("status"), screen=screen)
            if resumed or "config" not in rec:  # the login this process runs on; its waiter notices when
                config = os.environ.get("CLAUDE_CONFIG_DIR")  # the account under it changes
                rec.update(config=config, account=identity(config))
            fresh[0] = once(rec, "retry")
        return rec

    rec = retry_update(sid, change)
    details = " ".join(str(data.get("error_details") or "").split())[:160]
    if fresh[0]:
        retry_log(rec, f"> retry {rec['name']} · {time.strftime('%Y-%m-%d %H:%M')} · {kind}{' · ' + details if details else ''}")
    if not rec["pid"] or not (rec["resume"] if kind == "rate_limit" else
                              setting("autoretry", "off") == "on" and reachable()):
        return
    if kind in RETRY_KINDS + ("rate_limit",) or (kind == "max_output_tokens" and rec["attempts"] == 0):
        subprocess.Popen([sys.executable, __file__, "retry-wait", sid], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


def snapshot(path):
    try:
        st = Path(path).stat()
        return [st.st_size, st.st_mtime]
    except (OSError, TypeError):
        return None


def session_place(row):
    """Where the session a row of Claude Code's list names runs (a sessions file's dict will do), as
    (backend, place): a background session by its id, any other through its process in the terminal's
    backend. The place is None where that backend has none for it."""
    if row.get("kind") == "background":
        return BACKGROUND, BACKGROUND.locate(sid=row.get("sessionId"))
    return TMUX, TMUX.locate(pid=row["pid"]) if row.get("kind") == "interactive" and row.get("pid") else None


def send(row, text, how=backend.FREE):
    """A line as the next prompt of the session a row names, wherever it runs: (place, "") once typed,
    else (None, why not). Why: unreachable (it runs in no place of its backend), the backend's own
    (copy mode, no prompt box, dialog, draft, waiting, not settled, not stopped, not typed), busy (a
    background session at work, which is never stopped for a FREE line), or no answer (<error>) when
    the backend's program is missing or hangs."""
    try:
        at, found = session_place(row)
        if found is None:
            return None, "unreachable"
        if at is BACKGROUND and how == backend.FREE and backend.mode(found.row) == "busy":
            return None, "busy"
        why = at.type(found, text, how)
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"no answer ({type(e).__name__})"
    return (None, str(why)) if why else (found.id, "")


def type_into(row, text):
    """Type a line into the tmux pane of the session a `claude agents` row names, only into a free
    prompt box: (pane, "") once typed, else (None, why not). No tmux on the machine, or one that
    hangs, is a why like the others."""
    pane, why = send(row, text) if row.get("kind") == "interactive" else (None, "unreachable")
    why = {"unreachable": "not in a tmux pane", "not typed": "tmux did not take the line"}.get(why, why)
    return pane, f"tmux or ps did not answer{why[9:]}" if why.startswith("no answer") else why


def continue_session(rec, text):
    """Type `text` as the session's next prompt, or resume a background one with it, once every
    check passes: still idle, the pane is the live process's, not in copy mode, no draft."""
    if not session_file(rec["pid"], rec["session"]):
        return False
    if rec.get("background"):
        row = next((a for a in backend.agents() if isinstance(a, dict) and a.get("sessionId") == rec["session"]), None)
        if row is None or backend.mode(row) != "idle":
            return False
        wake("", text, sid=rec["session"])
        return True
    if session_file(rec["pid"], rec["session"]).get("status") != "idle":
        return False
    found = TMUX.locate(pid=rec["pid"])
    if found is None or found.copy or draft(found):
        return False
    return TMUX.type(found, text) == ""


def retry_wait(sid):
    """The waiter one StopFailure starts: locked on session id plus pid, the computer kept awake
    while it waits. It waits the next step of RETRY_STEPS (minutes, then every 30) and types one
    continue, which is its own probe: the next error starts a waiter that keeps the count, and the
    next Stop deletes the record. Gives up 12 hours after the first error."""
    rec = retry_record(sid)
    if not rec or not rec.get("pid"):
        return
    if rec["kind"] == "rate_limit":
        return limit_wait(sid)
    pid = rec["pid"]
    lock = RETRY / f"{sid}-{pid}.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            if alive(int(lock.read_text())):
                return
        except (OSError, ValueError):
            pass
        lock.unlink(missing_ok=True)
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    awake = None
    if shutil.which("caffeinate"):
        awake = subprocess.Popen(["caffeinate", "-i", "-w", str(pid)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    unit = float(os.environ.get("FLOWKIT_RETRY_MINUTE") or 60)
    try:
        at, seen = None, None
        while True:
            rec = retry_record(sid)
            if not rec or rec["pid"] != pid or not alive(pid) or rec["kind"] == "rate_limit" or \
                    time.time() > rec["first"] + RETRY_HOURS * 3600:
                return
            if rec["at"] != at:  # a new error: wait the next step from it, the count kept
                at = rec["at"]
                step = 0 if rec["kind"] == "max_output_tokens" else \
                    RETRY_STEPS[min(rec["attempts"], len(RETRY_STEPS) - 1)]
                settle = SETTLE * unit / 60
                due = at + max(step * unit, settle)
                time.sleep(max(at + settle - time.time(), 0))
                seen = snapshot(rec["transcript"])
            if time.time() < due:
                time.sleep(min(5, due - time.time()))
                continue
            if setting("autoretry", "off") != "on" or not reachable():
                return
            when = time.strftime("%H:%M", time.localtime(rec["at"]))
            if rec["kind"] == "max_output_tokens":
                text = (f"Your last turn hit the output token limit at {when}. Carry on from where you were, "
                        "in smaller pieces: shorter replies, smaller edits, one file at a time.")
            else:
                text = f"Your last turn ended on an API error ({rec['kind']}) at {when}. Carry on from where you were."
            now_seen = snapshot(rec["transcript"])
            if now_seen == seen and continue_session(rec, text):
                rec["attempts"] += 1
                (RETRY / f"{sid}.json").write_text(json.dumps(rec))
                return
            if rec["kind"] == "max_output_tokens":
                return
            seen, due = now_seen, time.time() + RETRY_STEPS[-1] * unit  # not idle or not safe: the next slot
    finally:
        if awake:
            awake.terminate()
        lock.unlink(missing_ok=True)


def transcript_tail(path):
    """The parsed lines of a transcript's end (its last TAIL bytes)."""
    try:
        with open(path, "rb") as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(size - TAIL, 0))
            lines = f.read().decode(errors="replace").splitlines()
    except (OSError, TypeError):
        return []
    rows = []
    for line in lines[size > TAIL:]:  # a cut starts mid-line
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def limit_line(rows):
    """Where the last usage-limit line sits in a transcript's rows, or None."""
    return next((i for i in range(len(rows) - 1, -1, -1)
                 if rows[i].get("isApiErrorMessage") and rows[i].get("error") == "rate_limit"), None)


def row_text(row):
    message = row.get("message") if isinstance(row.get("message"), dict) else {}
    content = message.get("content")
    if isinstance(content, list):
        content = " ".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
    return str(content or "")


def stamp(row):
    """A transcript line's time as an epoch; None for the harness's own lines, which carry none."""
    try:
        return datetime.fromisoformat(str(row.get("timestamp")).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def moved_on(rows, at):
    """The session went on after its last limit line: a model's reply or a prompt follows it. The
    harness's own lines (title, mode, bridge session) carry no time and never count, nor do the
    lines a /command or a ! shell line leaves, which someone looking at /usage writes. With no
    limit line in the tail, a line counts when it is stamped after the stop."""
    last = limit_line(rows)
    for row in rows if last is None else rows[last + 1:]:
        when = stamp(row)
        if when is None or row.get("type") not in ("user", "assistant") or row.get("isMeta") or \
                row.get("isApiErrorMessage") or (last is None and when <= at):
            continue
        if row["type"] == "assistant" or not row_text(row).lstrip().startswith(LOCAL):
            return True
    return False


def disconnected(rows, after):
    """When the transcript last noted, after `after`, that Remote Control dropped because the
    login changed under the session (an epoch), or None."""
    for row in reversed(rows):
        if row.get("type") == "system" and all(part in str(row.get("content") or "") for part in NOTICE):
            when = stamp(row)
            return when if when and when > after else None
    return None


def last_model(rows):
    """The model of the transcript's last real reply (a limit line's is <synthetic>), or ""."""
    for row in reversed(rows):
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        if row.get("type") == "assistant" and message.get("model") not in (None, "", "<synthetic>"):
            return str(message["model"])
    return ""


def usage_reading(rec):
    """A live usage reading taken as the login of a limit record's session, or None: never the
    cache or old figures (their reset may have passed), and never a reading of another account
    than the record's, which is what the other login on this config folder would answer."""
    try:
        out = subprocess.run([sys.executable, str(USAGE_BIN), "--json", "--live"],
                             capture_output=True, text=True, timeout=60, env=login_env(rec)).stdout
        reading = json.loads(out)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if not isinstance(reading, dict) or reading.get("source") != "live" or \
            ("account" in rec and reading.get("account") != rec["account"]):
        return None
    return reading


def usage_reset(reading, model, after):
    """From a usage reading, the latest reset among the capped buckets that bind a session on
    `model`: the week, the 5 hours and the model's own (the first word of its display name inside
    the model id; when none matches, every model's). None without a capped bucket that resets
    after `after`."""
    models = [m for m in reading.get("models") or [] if isinstance(m, dict)]
    own = [m for m in models if str(m.get("model") or "?").split()[0].lower() in model.lower()]
    resets = [b["resets_at"] for b in [reading.get("week"), reading.get("five_hour"), *(own or models)]
              if isinstance(b, dict) and isinstance(b.get("percent"), (int, float)) and b["percent"] >= 100
              and isinstance(b.get("resets_at"), (int, float)) and b["resets_at"] > after]
    return max(resets, default=None)


def spoken_reset(text, base):
    """The reset a limit message names ("resets 2am (Europe/Berlin)", "resets Nov 25, 2:30pm") as an
    epoch: the first such moment after `base`, in the zone it names, else the computer's. None when
    it names no time."""
    m = RESETS.search(text)
    if not m or not (m["ap"] or m["m"]):
        return None
    zone = None
    try:
        from zoneinfo import ZoneInfo
        zone = ZoneInfo(m["tz"]) if m["tz"] else None
    except Exception:  # no zoneinfo (Python 3.8), no such zone, no zone data
        pass
    start = datetime.fromtimestamp(base, zone) if zone else datetime.fromtimestamp(base).astimezone()
    try:
        hour = int(m["h"]) % 12 + (12 if m["ap"].lower() == "pm" else 0) if m["ap"] else int(m["h"])
        when = start.replace(hour=hour, minute=int(m["m"] or 0), second=0, microsecond=0)
        if m["mon"]:
            when = when.replace(month=MONTHS.index(m["mon"].lower()) + 1, day=int(m["day"]))
            if when <= start:
                when = when.replace(year=when.year + 1)
        elif when <= start:
            when += timedelta(days=1)
    except ValueError:
        return None
    return when.timestamp()


def limit_reset(rec, rows, now):
    """When the limit that stopped the session resets, as an epoch, from the first source that
    knows: the limit line's own quota answer, a live usage reading, the limit line's words; None
    when none does. Never a reset the session was already continued for. A reset that has passed
    counts only from the limit line (the computer slept through it), never from a reading."""
    floor = rec.get("continued_for") or 0
    last = limit_line(rows)
    line = rows[last] if last is not None else {}
    quota = line.get("quotaLimits") if isinstance(line.get("quotaLimits"), dict) else {}
    reset = quota.get("resetsAt") if isinstance(quota.get("resetsAt"), (int, float)) else None
    if reset and reset > floor:
        return reset
    reading = usage_reading(rec)
    live = usage_reset(reading, last_model(rows), max(now, floor)) if reading else None
    if live or reset:  # a quota answer already continued for: the line's words say nothing newer
        return live
    said = spoken_reset(row_text(line), stamp(line) or rec["at"])
    return said if said and said > floor else None


def limit_mark(sid, key=None, line=None, **fields):
    """Set fields of a limit record and, the first time for `key`, write `line` where its lines go."""
    tell = []

    def change(rec):
        if rec is None:
            return None
        rec.update(fields)
        if key and once(rec, key):
            tell.append(rec)
        return rec

    retry_update(sid, change)
    if tell:
        retry_log(tell[0], line)


def limit_pane(rec):
    """The pane of a limit record's session; None too when there is no tmux to ask."""
    try:
        return TMUX.locate(pid=rec["pid"])
    except (OSError, subprocess.SubprocessError):
        return None


def switched(sid, rec):
    """The account a limited session runs on changed since its record was written: the account file
    of its login names another account or organization (the ids compared, never the file's time: it
    is rewritten all day), and the session shows it too, its Remote Control link gone or its
    transcript saying it dropped for that reason. Returns who it is now, else None."""
    who = identity(rec.get("config")) if rec.get("account") else None
    if not who or who == rec["account"]:
        return None
    if session_file(rec["pid"], sid).get("bridgeSessionId") and \
            not disconnected(transcript_tail(rec.get("transcript")), rec.get("first") or 0):
        return None
    return who


def limit_switch(sid, who):
    """An account change counts as a reset. The record starts over as the new account's: no
    continue counted, no reset continued for, every line free to be said once more, and what it was
    left to is open again (the user, after three continues; Claude Code's own wait, which ends at
    an account change without continuing)."""
    def change(rec):
        if rec is None:
            return None
        for key in ("continued_for", "handled", "reset", "due"):
            rec.pop(key, None)
        rec.update(account=who, switched=time.time(), continues=0, lines=[], state="switched")
        return rec

    rec = retry_update(sid, change)
    if rec:
        retry_log(rec, f"> limit {rec['name']} · {time.strftime('%Y-%m-%d %H:%M')} · the account it runs on changed: "
                  "counted as a reset")


def after_switch(rec, unit):
    """The record's stop says nothing of the account the session runs on now: the account changed
    after it, or less than SWITCH_QUIET minutes before it (a request already under way, a notice
    that woke the session)."""
    return rec["at"] < rec.get("switched", 0) + SWITCH_QUIET * unit


def limit_plan(sid, rec, unit):
    """A new stop on the limit. Stores what the session looks like at rest and returns (when to
    continue, the reset that is for, or None when no source names one: then RETRY_STEPS, as after
    an API error). Returns None, the record marked handled, after LIMIT_CONTINUES continues that
    ended on the limit again, and for a session Claude Code's own wait covers: no Remote Control,
    autoContinueAtUsageLimit: true, the reset within NATIVE_HOURS. A stop from before an account
    change (after_switch) is planned from a live reading of the new account alone, its limit line
    being the old one's: no capped bucket binds the session's model, so it is continued at once
    (a stop that came after the change: once SWITCH_QUIET is over, and never counted as a continue
    that failed); one does, so after its reset; no reading, so like a stop no source names a reset for."""
    at, now = rec["at"], time.time()
    found = None if rec.get("background") else limit_pane(rec)
    rest = {"status": session_file(rec["pid"], sid).get("status"), "screen": TMUX.bottom(found) if found else []}
    head = f"> limit {rec['name']} · {time.strftime('%Y-%m-%d %H:%M')} · "
    fields = {}
    if after_switch(rec, unit):
        reading = usage_reading(rec)
        reset = usage_reset(reading, last_model(transcript_tail(rec.get("transcript"))), now) if reading else None
        quiet = rec["switched"] + SWITCH_QUIET * unit if at > rec["switched"] else now
        due = reset + LIMIT_GRACE * unit if reset else quiet if reading else \
            max(at, rec["switched"]) + RETRY_STEPS[0] * unit
        fields = {"continues": 0, "continued_for": None}
    else:
        if rec.get("continues", 0) >= LIMIT_CONTINUES:
            limit_mark(sid, "stopped", f"{head}not continued: {LIMIT_CONTINUES} continues ended on the limit again; "
                       "it waits for you", handled=at, state="unreachable", rest=rest)
            return None
        reset = limit_reset(rec, transcript_tail(rec.get("transcript")), now)
        if reset and not rec.get("remote") and not rec.get("background") and auto_continue() and \
                reset - now <= NATIVE_HOURS * 3600:
            limit_mark(sid, handled=at, state="native", rest=rest)
            return None
        due = reset + LIMIT_GRACE * unit if reset else \
            at + RETRY_STEPS[min(rec.get("continues", 0), len(RETRY_STEPS) - 1)] * unit
    limit_mark(sid, f"due {int(reset)}" if reset else None,
               f"{head}continues at {time.strftime('%Y-%m-%d %H:%M', time.localtime(due))}, once the limit has "
               "reset: the computer must be awake then", rest=rest, reset=reset, due=due, state="waiting", **fields)
    return due, reset


def limit_continue(rec, text):
    """continue_session for a limit stop, saying why not: "" once typed, "unreachable" when the
    session has no tmux pane to type into, else what the next slot may find gone (not idle, copy
    mode, and parked's: no prompt box, a dialog, a draft)."""
    if rec.get("background"):
        return "" if continue_session(rec, text) else "not idle"
    if session_file(rec["pid"], rec["session"]).get("status") != "idle":
        return "not idle"
    found = limit_pane(rec)
    if found is None:
        return "unreachable"
    return TMUX.type(found, text, backend.FREE)


def claimed(rec, now=None):
    """The claim in effect on a limit record, None without one: a hook outside flowkit took the stop
    over (`flowkit-session claim`), for the session's process as it was then and until its time."""
    claim = rec.get("claim")
    now = time.time() if now is None else now
    return claim if isinstance(claim, dict) and claim.get("pid") == rec.get("pid") and now < (claim.get("until") or 0) \
        else None


def limit_take(sid):
    """The waiter's word that it types its continue now, written under the records' lock, where a
    claim is written too: False when one holds, and then nothing is typed. So of a waiter and a
    claimer that come for one stop at the same moment only one types."""
    took = []

    def change(rec):
        if rec is None or claimed(rec):
            return None
        took.append(rec)
        return {**rec, "typing": time.time()}

    retry_update(sid, change)
    return bool(took)


def limit_unclaim(sid, claim, said):
    """A claim comes off its limit record: given back, or run out. One line says so, once per claim,
    unless it was for a process the session no longer runs in."""
    tell = []

    def change(rec):
        if rec is None or rec.get("claim") != claim:
            return None
        del rec["claim"]
        if claim.get("pid") == rec.get("pid") and once(rec, f"unclaim {claim.get('at')}"):
            tell.append(rec)
        return rec

    retry_update(sid, change)
    if tell:
        retry_log(tell[0], f"> limit {tell[0]['name']} · {time.strftime('%Y-%m-%d %H:%M')} · {said}: flowkit's waiter has "
                  "it again")
    return bool(tell)


def limit_lock(sid):
    """The limit waiter's lock: an flock on retry/<sid>.lock, which the kernel drops with its
    holder, so a dead waiter never blocks the next. The open file, or None when another waiter
    holds it (a few tries: the tick tests the lock for a moment, a waiter on its way out removes
    the file)."""
    path = RETRY / f"{sid}.lock"
    for _ in range(10):
        f = path.open("a")
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if os.fstat(f.fileno()).st_ino == path.stat().st_ino:
                f.truncate(0)
                f.write(str(os.getpid()))
                f.flush()
                return f
        except OSError:
            pass
        f.close()
        time.sleep(0.2)
    return None


def limit_wait(sid):
    """The waiter of a session stopped on a usage limit. One per record (limit_lock), alive until
    the record goes (a clean turn end) or the session does, so nothing starts a second one beside
    it. Each new stop is planned once (limit_plan). The computer is kept awake only from LIMIT_HOLD
    minutes before the continue. When that is due the session gets one line, if it has not gone on
    by itself and its pane can take it; else the next slot, or a line saying it waits for the
    user. No 12-hour cap and no away or autoretry test: limit resume is its own setting. An account
    change under the session (switched) is looked for at every nap, whatever the record was left
    to, and starts it over (limit_switch). While a hook outside flowkit holds a claim on the stop
    (claimed) the waiter stands down: it plans nothing and types nothing, and once the claim is
    given back or has run out it plans the stop again, as a new one."""
    lock = limit_lock(sid)
    if lock is None:
        return
    unit = float(os.environ.get("FLOWKIT_RETRY_MINUTE") or 60)
    nap = max(min(5.0, unit / 12), 0.05)
    awake, at, due, reset, look, stood = None, None, None, None, 0, False
    try:
        while True:
            rec = retry_record(sid)
            if not rec or rec.get("kind") != "rate_limit" or not rec.get("pid") or not alive(rec["pid"]) or \
                    not session_file(rec["pid"], sid):
                return
            if claimed(rec):
                stood = True
                time.sleep(nap)
                continue
            if isinstance(rec.get("claim"), dict):  # one that ran out, or of the process the session ran in before
                limit_unclaim(sid, rec["claim"], f"the claim of {rec['claim'].get('by')} ran out")
                stood = True
            if stood:
                stood, at = False, None
            if rec.get("account") and identity(rec.get("config")) not in (None, rec["account"]) and time.time() >= look:
                who = switched(sid, rec)
                if who:
                    limit_switch(sid, who)
                    at = None  # planned again, as the new account's
                    continue
                look = time.time() + unit / 4  # the session does not show it yet: its transcript is not read at every nap
            if (rec["at"] != at or (due is not None and time.time() >= due)) and not limit_resume():
                retry_set(sid, resume=False)  # turned off since: read before each plan and each continue
                return
            if rec["at"] != at:  # a new stop: the first, a continue that hit the limit again, a prompt typed meanwhile
                at, due = rec["at"], None
                if rec.get("handled") != at:
                    time.sleep(max(at + SETTLE * unit / 60 - time.time(), 0))
                    due, reset = limit_plan(sid, rec, unit) or (None, None)
                continue
            now = time.time()
            if due is None or now < due:
                if due is not None and awake is None and now >= due - LIMIT_HOLD * unit and shutil.which("caffeinate"):
                    awake = subprocess.Popen(["caffeinate", "-i", "-w", str(rec["pid"])], stdin=subprocess.DEVNULL,
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(nap if due is None else min(nap, due - now))
                continue
            if not limit_take(sid):  # claimed in this moment: the next pass stands down
                continue
            when = time.strftime("%H:%M", time.localtime(at))
            since = "it has reset"
            if after_switch(rec, unit):
                try:
                    reconnect(sid)  # its Remote Control first, so the turn that follows shows on the phone
                except Exception:
                    pass
                since = since if reset else "the account it runs on has changed since"
            why = "moved" if moved_on(transcript_tail(rec.get("transcript")), at) else limit_continue(
                rec, f"Your last turn stopped on the usage limit at {when}; {since}. Carry on from where you were.")
            head = f"> limit {rec['name']} · {time.strftime('%Y-%m-%d %H:%M')} · not continued: "
            if why == "":
                retry_update(sid, lambda r: r and {
                    **r, "continues": r.get("continues", 0) + 1, "continued_at": time.time(),
                    "continued_for": reset or time.time(), "handled": at, "state": "continued", "typing": None})
            elif why == "moved":
                limit_mark(sid, handled=at, state="moved", typing=None)
            elif why == "unreachable":
                limit_mark(sid, "unreachable", f"{head}it does not run in a tmux pane; it waits for you",
                           handled=at, state="unreachable", typing=None)
            else:  # at work, copy mode, a draft, a dialog: the next slot
                if after_switch(rec, unit) and time.time() < rec["switched"] + SWITCH_QUIET * unit:
                    limit_mark(sid, state=why, typing=None)
                    due = time.time() + unit / 2  # the account change itself may be what is in the way: soon again
                    continue
                limit_mark(sid, "dialog" if why == "dialog" else None,
                           f"{head}a dialog is open below its prompt box; looked at again every "
                           f"{RETRY_STEPS[-1]} minutes", state=why, typing=None)
                due = time.time() + RETRY_STEPS[-1] * unit
                continue
            due = None
            if awake:
                awake.terminate()
                awake = None
    finally:
        if awake:
            awake.terminate()
        Path(lock.name).unlink(missing_ok=True)
        lock.close()


def sessions():
    """The sessions file of every live session."""
    found = []
    for path in (CLAUDE / "sessions").glob("*.json"):
        info = session_file(path.stem) if path.stem.isdigit() else {}
        if info.get("sessionId") and alive(int(path.stem)):
            found.append({**info, "pid": int(path.stem)})
    return found


SESSION = """flowkit-session: a Claude Code session from outside it, for a hook or a script of your own.

  flowkit-session locate <session> [--json]
  flowkit-session read <session> [--rows <n>] [--box]
  flowkit-session type <session> <text> [--how free|line|over|escape|command]
  flowkit-session claim <session> --by <name> [--for <minutes>] [--wait <seconds>]
  flowkit-session release <session> --by <name>

<session> is a session id, a name as `claude agents` lists it, or a process id (digits alone).
`read` and `type` take `--place <id>` in its place: a place of the driver handoff.conf names (a tmux
pane's id, an outside driver's own), as `locate` printed it, a session in it or not.
`flowkit-session <command> --help` says what each prints. Every command exits

  0  done
  1  the driver's program (tmux, claude, flowkit-driver-<name>) did not answer
  2  called wrongly
  3  no session answers to <session>, or more than one does (they are listed)
  4  the session runs in no place flowkit reaches: outside tmux, or outside the driver handoff.conf names
  5  not done, and the reason is the one line it prints
"""
SESSION_HELP = {
    "locate": """flowkit-session locate <session> [--json]

Where a session runs and which story it was started for. Prints a line each, `key: value`:

  session     its session id
  name        its name, - without one
  pid         its process, - for a stopped background session
  kind        interactive or background
  status      idle, busy or waiting
  cwd         its folder
  backend     tmux, background, or the name of the outside driver
  place       the place's id there (a tmux pane's, the driver's own; a background session's id), - when it has none
  place_pid   the process the place itself started (a pane's shell), - where the backend names none
  copy        yes while the place is in tmux's copy mode, where typed keys reach no session
  story       the story's slug, - outside a story

--json prints the same as one object (null for -, true and false for copy). Later versions may add
keys. Exit 0, or 4 with the same lines and no place; 3 without a session; 1 when the driver did not answer.
""",
    "read": """flowkit-session read <session> [--rows <n>] [--box]
flowkit-session read --place <id> [--rows <n>] [--box]

What a session's place shows now: its rows as plain text, top to bottom, without the empty ones at
the end; --rows <n> gives the last n. Exit 5 and `no screen` when its backend shows none (a
background session, an outside driver that does not answer `screen`).

--box prints one word instead, what `type` would find: `free` (exit 0), or what is in a line's way
(exit 5): `copy mode`, `no prompt box`, `dialog`, `draft`; `waiting` or `busy` for a background
session. An outside driver without `screen` is `free`: there a line is typed without a look.
""",
    "type": """flowkit-session type <session> <text> [--how free|line|over|escape|command]
flowkit-session type --place <id> <text> [--how ...]

One line as the session's next prompt: the text, then Enter. Prints the id of the place it was typed
into (exit 0), or why it was not, one line (exit 5).

  --how free     the default: only where nothing is in its way. A prompt box on screen, empty, no
                 dialog below it, not in copy mode; a background session that is neither at work nor
                 waiting on a prompt (it is resumed with the text). Why not: `copy mode`,
                 `no prompt box`, `dialog`, `draft`, `waiting`, `busy`
  --how line     the text and Enter, without a look: for a place that runs a shell
  --how over     what is half typed is cleared first
  --how escape   Escape first, which ends the turn of a session that waits on a question, then as
                 over; a background session is stopped for it, at work or waiting
  --how command  a slash command, as free; a background session has no prompt box to take one

`not typed` (the driver refused the keys), `not stopped` (a background session's process did not
go) and `not settled` are reasons too. A line typed with --how free into a session at work is
queued by Claude Code as its next prompt.
""",
    "claim": """flowkit-session claim <session> --by <name> [--for <minutes>] [--wait <seconds>]

Takes over a session a usage limit stopped: from then on flowkit's own waiter does not continue it,
and whoever claimed it does what it wants with it (start it again elsewhere, type a line of its
own). Prints `claimed until <YYYY-MM-DD HH:MM>` (exit 0). The claim holds for --for minutes (15
without it; claim again under the same name to hold it longer), while the session runs in the same
process, and until `release`; the session's next clean turn end ends it with the stop's record.
The story file gets `> limit <session> · <time> · <name> took it over until <time>: flowkit does
not continue it meanwhile`.

Exit 5 when it is not yours to take, with one line: `flowkit continued it at <HH:MM>`, `flowkit is
typing its continue`, or `claimed by <other> until <HH:MM>`. Exit 3 when no usage-limit stop is
recorded for the session. A StopFailure hook of your own runs beside flowkit's, which writes that
record: `claim` waits --wait seconds for it (5 without it). Of a claim and flowkit's continue that
come at the same moment exactly one wins: a claim that printed `claimed` means nothing was typed.
""",
    "release": """flowkit-session release <session> --by <name>

Gives a claimed session back: flowkit's waiter plans the stop again and continues the session as it
would have. Prints `released` (exit 0); exit 3 when <name> holds no claim on it.
""",
}
HOWS = {"free": backend.FREE, "line": backend.LINE, "over": backend.OVER, "escape": backend.ESCAPE,
        "command": backend.COMMAND}
CLAIM = 15  # minutes a claim on a usage-limit stop holds when its claimer names no other time
CLAIM_WAIT = 5  # seconds `claim` waits for the stop's record: the claimer's hook and flowkit's run side by side
TYPING = 180  # seconds a waiter's word that it is typing counts: more than its slowest continue takes


def session_named(word):
    """The sessions that answer to a word: a process id (digits alone), a session id, else a name.
    The live ones from their session files; when none of them does, the newest background session
    of that id or name in Claude Code's own list, which keeps the stopped ones too."""
    live = sessions()
    if word.isdigit():
        return [i for i in live if i["pid"] == int(word)]
    found = [i for i in live if i["sessionId"] == word] or [i for i in live if i.get("name") == word]
    if not found:
        try:
            listed = [a for a in backend.agents("--all") if isinstance(a, dict) and a.get("kind") == "background"]
        except (OSError, subprocess.SubprocessError, ValueError):
            listed = []
        rows = [a for a in listed if a.get("sessionId") == word] or [a for a in listed if a.get("name") == word]
        found = [max(rows, key=lambda a: a.get("startedAt") or 0)] if rows else []
    return found


def session_box(at, found):
    """What is in the way of a FREE line at a place, "" when nothing is."""
    if at is BACKGROUND:
        return {"waiting": "waiting", "busy": "busy"}.get(backend.mode(found.row), "")
    return "copy mode" if found.copy else at.parked(found)


def session_claim(row, by, minutes, wait):
    """`flowkit-session claim`: the claim goes into the limit record of the session's live process,
    under the records' lock, unless flowkit's waiter has typed its continue for this stop, is typing
    it now, or another name holds the stop. (exit code, the line to print)."""
    sid, deadline = row["sessionId"], time.time() + wait
    clock = lambda at: time.strftime("%H:%M", time.localtime(at or 0))
    while True:
        out = {}

        def change(rec):
            now = time.time()
            if not rec or rec.get("kind") != "rate_limit" or not rec.get("pid") or rec["pid"] != row.get("pid"):
                return None
            held = claimed(rec, now)
            if held and held.get("by") != by:
                out.update(final=f"claimed by {held.get('by')} until {clock(held['until'])}")
            elif rec.get("handled") == rec.get("at") and rec.get("state") == "continued":
                out.update(final=f"flowkit continued it at {clock(rec.get('continued_at'))}")
            elif now - (rec.get("typing") or 0) < TYPING:
                out.update(busy="flowkit is typing its continue")
            else:
                rec["claim"] = {"by": by, "at": held["at"] if held else now, "until": now + minutes * 60, "pid": rec["pid"]}
                out.update(rec=rec, fresh=once(rec, f"claim {rec['claim']['at']}"))
                return rec
            return None

        retry_update(sid, change)
        if "rec" in out:
            until = time.strftime("%Y-%m-%d %H:%M", time.localtime(out["rec"]["claim"]["until"]))
            if out["fresh"]:
                retry_log(out["rec"], f"> limit {out['rec']['name']} · {time.strftime('%Y-%m-%d %H:%M')} · {by} took it "
                          f"over until {until[-5:]}: flowkit does not continue it meanwhile")
            return 0, f"claimed until {until}"
        if "final" in out:
            return 5, out["final"]
        if time.time() >= deadline:
            return (5, out["busy"]) if "busy" in out else (3, "")
        time.sleep(0.1)


def session_cmd(argv):
    """`handoff.py session …`, which bin/flowkit-session runs: the exit code, what is printed on the
    way. The commands reach a session through what flowkit's own lines go through (session_place,
    send, the limit record's lock)."""
    verb, args = (argv[0], argv[1:]) if argv else ("", [])
    if verb not in SESSION_HELP or "--help" in args or "-h" in args:
        asked = verb in SESSION_HELP or verb in ("--help", "-h", "help")
        print(SESSION_HELP.get(verb, SESSION), end="", file=sys.stdout if asked else sys.stderr)
        return 0 if asked else 2
    takes = {"locate": {"--json": 0}, "read": {"--rows": 1, "--box": 0, "--place": 1}, "type": {"--how": 1, "--place": 1},
             "claim": {"--by": 1, "--for": 1, "--wait": 1}, "release": {"--by": 1}}[verb]
    opts, words, wrong = {}, [], False
    while args:
        word = args.pop(0)
        if word == "--":  # what follows is no option: a text that starts with two dashes
            words, args = words + args, []
        elif word in takes and len(args) >= takes[word]:
            opts[word] = args.pop(0) if takes[word] else True
        elif word.startswith("--"):
            wrong = True
        else:
            words.append(word)
    number = lambda key: opts[key].replace(".", "", 1).isdigit() if key in opts else True
    placed = "--place" in opts
    if wrong or len(words) != (verb == "type") + (not placed) or opts.get("--how", "free") not in HOWS \
            or (verb in ("claim", "release") and not SAFE.match(opts.get("--by") or "")) \
            or not (opts.get("--rows") or "1").isdigit() or not int(opts.get("--rows") or 1) \
            or not number("--for") or not number("--wait"):
        print(SESSION_HELP[verb], end="", file=sys.stderr)
        return 2
    at = TMUX
    try:
        if placed:
            row, found = None, backend.Place(opts["--place"], False, None)
        else:
            rows = session_named(words[0])
            if len(rows) != 1:
                print(f"flowkit-session: {'no session answers' if not rows else f'{len(rows)} sessions answer'} to "
                      f"{words[0]!r}" + "".join(f"\n  {r.get('sessionId')}  pid {r.get('pid') or '-'}  {r.get('name') or '-'}"
                                                for r in rows), file=sys.stderr)
                return 3
            row = rows[0]
            if verb == "claim":
                code, said = session_claim(row, opts["--by"], float(opts.get("--for", CLAIM)),
                                           float(opts.get("--wait", CLAIM_WAIT)))
                print(said or f"flowkit-session: no usage-limit stop is recorded for {row.get('name') or row['sessionId']}",
                      file=sys.stdout if said else sys.stderr)
                return code
            if verb == "release":
                rec = retry_record(row["sessionId"]) or {}
                claim = rec.get("claim") if isinstance(rec.get("claim"), dict) and rec["claim"].get("by") == opts["--by"] else None
                if claim is None:
                    print(f"flowkit-session: {opts['--by']} holds no claim on {row.get('name') or row['sessionId']}", file=sys.stderr)
                    return 3
                limit_unclaim(row["sessionId"], claim, f"{opts['--by']} gave it back")
                print("released")
                return 0
            at, found = session_place(row)
        if verb == "locate":
            known = {"session": row.get("sessionId"), "name": row.get("name"), "pid": row.get("pid"), "kind": row.get("kind"),
                     "status": backend.mode(row), "cwd": row.get("cwd"), "backend": at.name,
                     "place": found.id if found else None, "place_pid": None, "copy": bool(found and found.copy),
                     "story": (at.story(found) if found else "") or None}
            if found and at is not BACKGROUND:
                try:
                    top = next((p.get("pid") for p in at.places() if p.get("id") == found.id), "")
                except (OSError, subprocess.SubprocessError, KeyError, ValueError):
                    top = ""  # a list of places that cannot be read takes nothing from what is known
                known["place_pid"] = int(top) if str(top).isdigit() else None
            if "--json" in opts:
                print(json.dumps(known))
            else:
                for key, value in known.items():
                    print(f"{key}: {('yes' if value else 'no') if key == 'copy' else '-' if value is None else value}")
            return 0 if found else 4
        if found is None:
            print(f"flowkit-session: {row.get('name') or row['sessionId']} runs in no place of {at.name}", file=sys.stderr)
            return 4
        if verb == "read" and "--box" in opts:
            why = session_box(at, found)
            print(why or "free")
            return 5 if why else 0
        if verb == "read":
            shown = [text for text, _ in at.screen(found)]
            while shown and not shown[-1].strip():
                shown.pop()
            print("\n".join(shown[-int(opts["--rows"]):] if "--rows" in opts else shown) if shown else "no screen")
            return 0 if shown else 5
        how = HOWS[opts.get("--how", "free")]
        if placed:
            why = at.type(found, words[-1], how)
            place, why = (None, str(why)) if why else (found.id, "")
        else:
            place, why = send(row, words[-1], how)
    except (OSError, subprocess.SubprocessError) as e:
        print(f"flowkit-session: {at.name} did not answer ({type(e).__name__})", file=sys.stderr)
        return 1
    if why.startswith("no answer"):
        print(f"flowkit-session: {at.name} did not answer{why[9:]}", file=sys.stderr)
        return 1
    print(why or place)
    return 5 if why else 0


def reconnect(sid, patience=0):
    """One session's Remote Control after the account of its login changed. Claude Code drops the
    link then, says so in the transcript and brings it back only when asked, so `/remote-control`
    is typed for the session: once per notice, and only while it shows no link, is idle (not at
    work, not on a question) and its prompt box is on screen, empty, with no dialog below it. Until
    then it stays owed (<sid>.owed, which keeps the notice's time: a session at work can write the
    notice out of the transcript's tail), for the next turn end and the tick. The link has REMOTE_WAIT
    seconds to show; when it does not, a dialog the command left open is closed with Esc and one
    line says so. A session whose link stands gets nothing, nor does one that never said it lost
    it to an account change; no session is ever restarted for this. Returns what was typed, or ""."""
    info = next((i for i in sessions() if i["sessionId"] == sid), None)
    mark, owed = REMOTE / f"{sid}.json", REMOTE / f"{sid}.owed"
    if info is None or info.get("kind") != "interactive" or claimed(retry_record(sid) or {}):
        return ""
    unit = float(os.environ.get("FLOWKIT_RETRY_MINUTE") or 60) / 60
    REMOTE.mkdir(parents=True, exist_ok=True)
    with (REMOTE / f"{sid}.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)  # the tick's pass, a turn end and a limit waiter may all come for it
        pid, fresh = info["pid"], lambda: session_file(info["pid"], sid)
        if fresh().get("bridgeSessionId"):
            owed.unlink(missing_ok=True)
            return ""
        try:
            done = json.loads(mark.read_text()).get("notice") or 0
        except (OSError, ValueError, AttributeError):
            done = 0
        rows = [r for path in (CLAUDE / "projects").glob(f"*/{sid}.jsonl") for r in transcript_tail(path)]
        notice = disconnected(rows, done)
        if not notice:  # none in the tail: what the session is owed for still counts
            try:
                notice = float(owed.read_text())
            except (OSError, ValueError):
                notice = 0
            notice = notice if notice > done else None
        if not notice or any(r.get("subtype") == "bridge_status" and (stamp(r) or 0) > notice for r in rows):
            owed.unlink(missing_ok=True)  # nothing new since the last one handled, or the link came back after it
            return ""
        deadline = time.time() + patience * unit
        while fresh().get("status") != "idle" and time.time() < deadline:
            time.sleep(0.2)
        try:
            pane = TMUX.locate(pid=pid) if fresh().get("status") == "idle" else None
            typed = pane is not None and TMUX.type(pane, "/remote-control", backend.COMMAND) == ""
        except (OSError, subprocess.SubprocessError):
            typed = False
        if not typed:
            owed.write_text(str(notice))
            return ""
        mark.write_text(json.dumps({"notice": notice, "at": time.time(), "state": "typed"}))
        owed.unlink(missing_ok=True)
        deadline = time.time() + REMOTE_WAIT * unit
        while time.time() < deadline and not fresh().get("bridgeSessionId"):
            time.sleep(min(0.5, REMOTE_WAIT * unit / 10))
        back = bool(fresh().get("bridgeSessionId"))
        if not back and fresh():
            left = parked(pane) == "dialog"
            if left:
                TMUX.escape(pane)
            story = TMUX.story(pane)
            retry_log({"story": story if SAFE.match(story) else ""},
                      f"> remote {info.get('name') or sid[:8]} · {time.strftime('%Y-%m-%d %H:%M')} · Remote Control did not "
                      f"come back after the account changed{': the dialog it left open was closed' if left else ''}; "
                      "type /remote-control in the session")
        mark.write_text(json.dumps({"notice": notice, "at": time.time(), "state": "back" if back else "failed"}))
        return "/remote-control"


def reconnect_pass(every):
    """The tick's pass: every session that is owed its Remote Control, or with `every` each live
    interactive one that shows no link (its transcript says whether it lost one to an account
    change), at the same time; first the files of sessions that are gone go, and what is owed to a
    session whose link is back (the user typed /remote-control themselves)."""
    live = {i["sessionId"]: i for i in sessions()}
    REMOTE.mkdir(parents=True, exist_ok=True)
    for path in REMOTE.iterdir():
        if path.suffix in (".json", ".owed", ".lock") and path.stem not in live or \
                path.suffix == ".owed" and live[path.stem].get("bridgeSessionId"):
            path.unlink(missing_ok=True)
    due = [sid for sid, i in live.items() if i.get("kind") == "interactive" and not i.get("bridgeSessionId")
           and (every or (REMOTE / f"{sid}.owed").exists())]
    if due:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(reconnect, due))


def api_ok_hook():
    """Stop: the turn ended cleanly, so the session's retry record goes; a session owed its Remote
    Control since an account change gets it now, from a process of its own."""
    sid = str(json.load(sys.stdin).get("session_id") or "")
    if SAFE.match(sid) and (RETRY / f"{sid}.json").exists():
        with retry_lock():
            (RETRY / f"{sid}.json").unlink(missing_ok=True)
    if SAFE.match(sid) and (REMOTE / f"{sid}.owed").exists():
        subprocess.Popen([sys.executable, __file__, "reconnect", sid], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wake(slug, name, patience=0, sid=None):
    """Ring a background reporter (with sid: resume that background session with `name` as the prompt). `claude --bg --resume` hands a stopped session the prompt under
    its own id; a live one would get a copy instead, so a live one is stopped first. Never while it
    waits on a prompt; while it is mid-turn, a process of its own waits up to five minutes, so the
    close does not."""
    reporter = BACKGROUND.locate(sid=sid, name=f"{slug}-report")
    deadline = time.time() + patience
    while reporter is not None and backend.mode(reporter.row) == "busy":
        if time.time() >= deadline:
            if patience == 0:
                again = ["--id", sid, name] if sid else [slug, name]
                subprocess.Popen([sys.executable, __file__, "wake", *again], start_new_session=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        time.sleep(10)
        reporter = BACKGROUND.locate(sid=sid, name=f"{slug}-report")
    if reporter is not None:
        BACKGROUND.type(reporter, name if sid else f"closed {name}", backend.FREE)


def ring(slug, name):
    """Tell the story's reporter `closed <name>`: a background one is woken with it as its prompt,
    a tmux one gets it typed into its pane when it is live and not waiting on a prompt. The file is
    the record; this is only the doorbell, so a miss never fails the close. Returns what happened:
    rung, background (woken), waiting (found, but at a prompt) or none (no reporter runs)."""
    if not BACKGROUND.available():
        return "none"
    try:
        agents = backend.agents("--all")
        if not isinstance(agents, list):
            return "none"
        if any(isinstance(a, dict) and a.get("kind") == "background" and a.get("name") == f"{slug}-report"
               for a in agents):
            wake(slug, name)
            return "background"
        if not TMUX.available():
            return "none"
        where = TMUX.finder()
        found = next(((where(a["pid"]), a.get("status")) for a in agents if a.get("kind") == "interactive"
                      and a.get("name") == f"{slug}-report" and a.get("pid") and where(a["pid"])), None)
        if found is None:
            return "none"
        if found[1] == "waiting":
            return "waiting"
        TMUX.type(found[0], f"closed {name}", backend.OVER)
        return "rung"
    except Exception:  # the record is already written; a doorbell never fails the close
        return "none"


def hid_idle():
    """Seconds since the last keyboard or mouse input at this computer (macOS's HID idle time); None
    where `ioreg` does not answer."""
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=10).stdout
        found = re.search(r'"HIDIdleTime" = (\d+)', out)
        return int(found[1]) / 1e9 if found else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def idle_seconds(now):
    """Seconds since the user's last keyboard or mouse input: macOS's HID idle time, elsewhere the
    newest activity of a tmux client; None when neither answers."""
    idle = hid_idle()
    if idle is not None:
        return idle
    try:
        seen = TMUX.last_input()
    except (OSError, subprocess.TimeoutExpired):
        seen = None
    return max(0, now - seen) if seen is not None else None


def remind_quiet(now):
    """Why a reminder says nothing now, a few words each: reminders are off, the user is away (they
    asked for silence) or at the computer (input in the last REMIND_IDLE minutes)."""
    why = [] if setting("remind", "off") == "on" else ["off (remind=on in handoff.conf turns it on)"]
    since = away_since()
    if since:
        why.append(f"away since {since}")
    idle = idle_seconds(now)
    if idle is not None and idle < REMIND_IDLE * 60:
        why.append(f"at the computer (last input {int(idle)} s ago)")
    return why


def remind_found(now):
    """What waits on the user, one (key, at once, text) each, and whether parked.json is too old to
    say anything. At once: what Claude Code's own pushes cannot see (a pane parked at a start-up
    prompt, a usage-limit stop flowkit cannot continue, an item turning ready for a device test).
    The rest (a session waiting on a prompt, an item ready to test) is for the digest alone."""
    found, stale = [], False
    try:
        data = json.loads(PARKED.read_text())
        stale = now - data["at"] > PARKED_FRESH
        for p in [] if stale else data["panes"]:
            where = f"tab {p['tab']}, pane {p['index']}" + (f", story {p['story']}" if p.get("story") else "")
            found.append((f"parked:{p['pane']}:{p['since']}", True, f"a pane is parked at a start-up prompt: {p['line']} ({where})"))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    for path in sorted(RETRY.glob("*.json")):
        rec = retry_record(path.stem)
        if isinstance(rec, dict) and rec.get("kind") == "rate_limit" and rec.get("state") == "unreachable":
            found.append((f"limit:{path.stem}:{rec.get('at')}", True,
                          f"{rec.get('name') or path.stem[:8]} stopped on the usage limit and flowkit cannot continue it"
                          + (f" (story {rec['story']})" if rec.get("story") else "")))
    try:
        rows = backend.agents()
    except (OSError, subprocess.TimeoutExpired, ValueError):
        rows = []
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("status") == "waiting":
            what = row.get("waitingFor") or "a prompt"
            found.append((f"waiting:{row.get('sessionId') or row.get('name')}:{what}", False,
                          f"{row.get('name') or '?'} waits on {what}"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import capacity  # the test queue's reader and its one rule about a gate
    for path in sorted(STORIES.glob("*.tests.md")):
        story = path.name[:-len(".tests.md")]
        for line in capacity.read_tests(story)[1]:
            gate = capacity.gate_of(line)
            if capacity.TO_TEST.search(line[3]) and not capacity.ON_GREEN.match(gate):
                found.append((f"test:{story}:{line[0]}", bool(capacity.DEVICE.search(gate)),
                              f"{story}: {line[0]} is ready to test" + (f" on {line[2]}" if line[2] else "")
                              + (f" · Gate: {gate}" if gate else "")))
    return found, stale


def remind_state():
    try:
        data = json.loads(REMIND.read_text())
    except (OSError, ValueError):
        data = None
    return data if isinstance(data, dict) else {}


def remind_mark(state):
    """Keep what a reminder has said, once it is said."""
    REMIND.parent.mkdir(parents=True, exist_ok=True)
    fresh = REMIND.with_suffix(".tmp")
    fresh.write_text(json.dumps(state))
    os.replace(fresh, REMIND)


def remind_pass(found, stale, said, now):
    """One look at what waits: (heading, lines, state), no lines when there is nothing to say.
    Every REMIND_EVERY minutes the digest, everything found, unless it is the set the last look
    of that kind saw. Else what is said at once and no reminder has said yet. The state is what
    to keep once the lines are said: a key that is gone is forgotten, so an item that turns ready
    again is said again, except a parked pane's while parked.json is too old to tell."""
    keys = {key for key, _, _ in found}
    once = said.get("once") if isinstance(said.get("once"), dict) else {}
    once = {k: v for k, v in once.items() if k in keys or (stale and k.startswith("parked:"))}
    digest = said.get("digest") if isinstance(said.get("digest"), dict) else {}
    heading, lines = "Needs you now", [text for key, at_once, text in found if at_once and key not in once]
    if now - (digest.get("at") or 0) >= REMIND_EVERY * 60:
        if keys and keys != set(digest.get("keys") or []):
            heading, lines = "Waiting on you", [text for _, _, text in found]
        digest = {"at": now, "keys": sorted(keys)}
    once.update({key: now for key, at_once, _ in found if at_once and key not in once})
    return heading, lines, {"once": once, "digest": digest}


def remind_file(heading, lines, now):
    """The reminder's text in flowkit/remind/<stamp>.md and flowkit/remind-latest.md; the newest
    file again when it says the same (a pass whose push did not arrive comes back a minute later)."""
    body = "".join(f"- {x}\n" for x in lines)
    REMIND_DIR.mkdir(parents=True, exist_ok=True)
    last = max(REMIND_DIR.glob("*.md"), default=None)
    try:
        old = last.read_text() if last else ""
    except OSError:
        old = ""
    if old.startswith(f"# {heading} · ") and old.partition("\n\n")[2] == body:
        return last
    path = REMIND_DIR / time.strftime("%Y%m%d-%H%M%S.md", time.localtime(now))
    text = f"# {heading} · {time.strftime('%Y-%m-%d %H:%M', time.localtime(now))}\n\n{body}"
    path.write_text(text)
    (REMIND_DIR.parent / "remind-latest.md").write_text(text)
    return path


def remind_push(heading, lines):
    """The push: one line of at most 195 characters, the first thing that waits and how many more."""
    more = f" (+{len(lines) - 1} more in remind-latest.md)" if len(lines) > 1 else ""
    first = " ".join(lines[0].split())
    room = 195 - len(heading) - len(": ") - len(more)
    return f"{heading}: {first if len(first) <= room else first[:room - 1].rstrip() + '…'}{more}"


def story_reported(slug):
    """The story's last section is its reporter's final report: its reporter has nothing left to collect."""
    try:
        heads = re.findall(r"^## (.+?) · \d{4}-\d\d-\d\d", (STORIES / f"{slug}.md").read_text(errors="replace"), re.M)
    except OSError:
        return False
    return bool(heads) and heads[-1] == "Final report"


def pushed(sid, after):
    """The answer to the session's first PushNotification call made after `after` ("error: …" for a
    failed one), or None while there is none or it has no answer yet."""
    path = next(CLAUDE.glob(f"projects/*/{sid}.jsonl"), None)
    calls = set()
    for row in transcript_tail(path) if path else []:
        message = row.get("message") if isinstance(row.get("message"), dict) else {}
        content = message.get("content")
        for block in content if isinstance(content, list) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use" and block.get("name") == "PushNotification" and (stamp(row) or 0) >= after:
                calls.add(block.get("id"))
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in calls:
                text = block.get("content")
                if isinstance(text, list):
                    text = " ".join(str(b.get("text") or "") for b in text if isinstance(b, dict))
                return f"error: {text}" if block.get("is_error") else str(text or "")
    return None


def remind_ring(row, line):
    """Ring one reporter: the line typed into its pane as the last call types it, then up to
    REMIND_WAIT seconds for the answer to the push it sends after the wait the line asks for. (sent,
    what happened); a push the tool did not send to the phone ("Not sent — this terminal is active",
    "Mobile push not sent (Remote Control inactive)", an error) is not sent."""
    at = time.time()
    why = type_into(row, line)[1]
    if why:
        return False, why
    while True:
        result = pushed(row["sessionId"], at)
        if result is not None:
            return "not sent" not in result.lower() and not result.startswith("error: "), result
        if time.time() - at >= REMIND_WAIT:
            return False, f"no push in {REMIND_WAIT} s"
        time.sleep(REMIND_WAIT / 18)


def remind_deliver(push, path):
    """Send the push through the first that carries it: the oldest idle reporter in a tmux pane whose
    story has no final report after its last close, then the next one; else the `notify` command of
    handoff.conf, the push on its stdin; else a macOS notification. Returns what carried it, or ""."""
    line = REMIND_RING.format(quiet=REMIND_QUIET, path=home_short(path), push=push)
    try:
        rows = [r for r in backend.agents() if isinstance(r, dict)]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        rows = []
    for row in sorted(rows, key=lambda r: r.get("startedAt") or 0):
        name = str(row.get("name") or "")
        if not name.endswith("-report") or row.get("kind") != "interactive" or backend.mode(row) != "idle" \
                or not row.get("pid") or not row.get("sessionId") or story_reported(name[:-len("-report")]):
            continue
        sent, what = remind_ring(row, line)
        print(f"ring {name}: {what}")
        if sent:
            return name
    command = setting("notify", "")
    if command:
        try:
            ok = subprocess.run(command, shell=True, input=push, capture_output=True, text=True, timeout=60).returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        print(f"notify: {'sent' if ok else 'failed'}")
        if ok:
            return "notify"
    try:
        ok = subprocess.run(["osascript", "-e", "on run argv", "-e", 'display notification (item 1 of argv) with title "flowkit"',
                             "-e", "end run", push], capture_output=True, text=True, timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    return "macOS notification" if ok else ""


def remind():
    """One pass, the tick's (remind=on): what waits on the user, sent while they are neither away nor
    at the computer; kept as said only once a push arrived. One that nothing carried is tried again
    REMIND_RETRY minutes later, not at every pass (each try rings every idle reporter), unless what
    waits has changed. One pass at a time: a delivery can take minutes."""
    REMIND.parent.mkdir(parents=True, exist_ok=True)
    with REMIND.with_suffix(".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("remind: another pass is running")
            return
        now = time.time()
        quiet = remind_quiet(now)
        if quiet:
            print(f"remind: silent, {'; '.join(quiet)}")
            return
        found, stale = remind_found(now)
        said = remind_state()
        heading, lines, state = remind_pass(found, stale, said, now)
        if not lines:
            remind_mark(state)
            print("remind: nothing to say")
            return
        clock = lambda when: time.strftime("%H:%M", time.localtime(when))
        failed = said.get("failed") if isinstance(said.get("failed"), dict) else {}
        if failed.get("lines") == lines and now < (failed.get("at") or 0) + REMIND_RETRY * 60:
            print(f"remind: not delivered at {clock(failed['at'])}, next try {clock(failed['at'] + REMIND_RETRY * 60)}")
            return
        path = remind_file(heading, lines, now)
        how = remind_deliver(remind_push(heading, lines), path)
        remind_mark(state if how else {**said, "failed": {"at": time.time(), "lines": lines}})
        print(f"remind: sent through {how} ({home_short(path)})" if how else
              f"remind: not delivered, next try {clock(time.time() + REMIND_RETRY * 60)} ({home_short(path)})")


def remind_dry():
    now = time.time()
    quiet = remind_quiet(now)
    found, stale = remind_found(now)
    heading, lines, _ = remind_pass(found, stale, remind_state(), now)
    print(f"remind: silent, {'; '.join(quiet)}" if quiet else "remind: not silent")
    print("\n".join([f"{heading}:"] + [f"- {x}" for x in lines]) if lines else "nothing to say")


def record(story):
    slug = slugify(story)
    STORIES.mkdir(parents=True, exist_ok=True)
    path = STORIES / f"{safe(slug, 'the story slug')}.md"
    text = sys.stdin.read().strip("\n")
    if not text:
        fail("nothing to record: the close goes on stdin")
    section = re.match(r"## (.+?) · \d{4}-\d{2}-\d{2} \d{2}:\d{2}\s*$", text.splitlines()[0])
    if not section:  # the heading the reporter, the board and the tmux kit read a close by
        fail("nothing recorded: the first line must be `## <session name> · YYYY-MM-DD HH:MM`, "
             f"not {text.splitlines()[0]!r}")
    head = "" if path.exists() else f"# {slug}\n"
    with path.open("a") as f:
        f.write(f"{head}\n{text}\n")
    print(path)
    if section.group(1) != "Final report":
        print(f"reporter: {ring(slug, section.group(1))}")
    drain(text)
    watch_poller()


def watch_poller():
    """An open watch with no poller gets one (`watch.py ensure`, detached): a close is one of the
    moments that is checked. No watch costs one directory read."""
    try:
        if any(e.name.endswith(".json") for e in os.scandir(CLAUDE / "flowkit" / "watches")):
            subprocess.Popen([sys.executable, str(Path(__file__).resolve().parent / "watch.py"), "ensure"],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
    except OSError:
        pass


def drain(text):
    """A close can free a slot or a resource: `capacity.py drain` launches what the capacity queue
    holds that can start now, the closing session counted as gone though it is still busy. An
    empty queue costs one directory read; a failure never fails the close."""
    try:
        if not any(e.name.endswith(".json") for e in os.scandir(CLAUDE / "capacity" / "queue")):
            return
    except OSError:
        return
    sid = re.search(r"^Session: ([0-9a-f-]{36})\b", text, re.M)
    try:
        out = subprocess.run([sys.executable, str(Path(__file__).resolve().parent / "capacity.py"), "drain",
                              *(["--closed", sid.group(1)] if sid else [])], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return
    for line in out.stdout.splitlines():
        print(f"capacity: {line}")


def chain_end_conf():
    """The chain_end= of handoff.conf, as `state` prints it: the value and what it means."""
    value = setting("chain_end", CHAIN_END)
    return f"{value} ({CHAIN_ENDS.get(value) or 'not one of ' + ', '.join(CHAIN_ENDS)})"


def chain_end(story, text=None):
    """The story file's `Chain end:` line, in its header above the first close: what the chain's last
    slice leaves behind, which every brief of the chain repeats. Prints it, or `none`; with text,
    writes it (a chain_end value with its meaning), creating the file as record does."""
    slug = safe(slugify(story), "the story slug")
    path = STORIES / f"{slug}.md"
    lines = path.read_text().splitlines() if path.is_file() else [f"# {slug}"]
    head = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
    have = next((i for i in range(head) if lines[i].startswith("Chain end:")), None)
    if text is None:
        print(lines[have].partition(":")[2].strip() if have is not None else "none")
        return
    text = " ".join(text.split())
    if not text:
        fail("the Chain end line needs words: a chain_end value or your own")
    line = f"Chain end: {text} — {CHAIN_ENDS[text]}" if text in CHAIN_ENDS else f"Chain end: {text}"
    if have is not None:
        lines[have] = line
    else:
        lines.insert(1 if lines and lines[0].startswith("# ") else 0, line)
    STORIES.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")
    print(line)


def conf(key, value):
    """Set one key=value line of handoff.conf, keeping every other line as it is."""
    if not re.fullmatch(r"[a-z_]+", key) or "\n" in value:
        fail(f"not a handoff.conf setting: {key}={value}")
    if (key, value) == ("idle_release", "on") and not shutil.which("ioreg"):
        fail("idle_release=on needs macOS: it tells a prompt typed at the computer from one sent from a phone by "
             "the time since the last key or mouse input (ioreg), which this system does not report")
    lines = SETTINGS_FILE.read_text().splitlines() if SETTINGS_FILE.is_file() else []
    lines = [l for l in lines if l.partition("=")[0].strip() != key] + [f"{key}={value}"]
    CONFIG.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text("\n".join(lines) + "\n")
    print(f"{SETTINGS_FILE}: {key}={value}")


def user_settings(action, pairs):
    """Show, or write with a backup, values in the user's settings.json: `key=<json>`, a dotted key
    nesting (`worktree.bgIsolation="none"`)."""
    text = settings_text()
    before = json.loads(text) if text.strip() else {}
    settings = json.loads(text) if text.strip() else {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        try:
            value = json.loads(raw)
        except ValueError:
            fail(f"not key=<json>: {pair}")
        if not sep or not key:
            fail(f"not key=<json>: {pair}")
        *parents, leaf = key.split(".")
        node = settings
        for part in parents:
            node = node.setdefault(part, {})
            if not isinstance(node, dict):
                fail(f"{part} in {SETTINGS} is not an object")
        node[leaf] = value
    if settings == before:
        print(f"settings: already set in {SETTINGS}")
        return
    new = json.dumps(settings, indent=2, ensure_ascii=False) + "\n"
    if action == "diff":
        sys.stdout.writelines(difflib.unified_diff(
            text.splitlines(True), new.splitlines(True), str(SETTINGS), f"{SETTINGS} (after)"))
        return
    if text:
        backup = SETTINGS.with_name(SETTINGS.name + ".flowkit-bak")
        backup.write_text(text)
        print(f"backup: {backup}")
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(new)
    print(f"settings: written to {SETTINGS} (applies to the next session)")


def settings_text():
    try:
        return SETTINGS.read_text()
    except FileNotFoundError:
        return ""


def proposed(text):
    """The settings text with the status line installed, or None when it already is."""
    settings = json.loads(text) if text.strip() else {}
    ours = f'python3 "{STATUSLINE}"'
    current = settings.get("statusLine") or {}
    command = current.get("command", "")
    if str(STATUSLINE) in command:
        return None
    if current.get("type") == "command" and command:
        line = dict(current, command=f"{ours} --then {shlex.quote(command)}")
    else:
        line = {"type": "command", "command": ours}
    settings["statusLine"] = line
    return json.dumps(settings, indent=2, ensure_ascii=False) + "\n"


def statusline(action, root):
    text = settings_text()
    new = proposed(text)
    if new is None:
        print(f"statusline: installed ({STATUSLINE} is in {SETTINGS})")
        return
    if action == "diff":
        print(f"statusline: not installed; install copies statusline.py to {STATUSLINE} and changes {SETTINGS}:")
        sys.stdout.writelines(difflib.unified_diff(
            text.splitlines(True), new.splitlines(True), str(SETTINGS), f"{SETTINGS} (after)"))
        return
    CONFIG.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(root) / "scripts" / "statusline.py", STATUSLINE)
    if text:
        backup = SETTINGS.with_name(SETTINGS.name + ".flowkit-bak")
        backup.write_text(text)
        print(f"backup: {backup}")
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(new)
    print(f"statusline: installed; {SETTINGS} now runs {STATUSLINE} (applies to the next session)")


def user_tmux_conf():
    for path in (HOME / ".tmux.conf", CONFIG.parent / "tmux" / "tmux.conf"):
        if path.is_file():
            return path
    return HOME / ".tmux.conf"


def home_short(path):
    return "~/" + str(path.relative_to(HOME)) if path.is_relative_to(HOME) else str(path)


def kit_version(text):
    try:
        return tuple(int(n) for n in text.strip().split("."))
    except (AttributeError, ValueError):
        return None


def plugin_version(root):
    try:
        return json.loads((Path(root) / ".claude-plugin" / "plugin.json").read_text())["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def copy_kit(root, files=KIT):
    for rel, dest in files.items():
        source = Path(root) / rel
        if not dest.is_file() or dest.read_bytes() != source.read_bytes():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
        if rel.startswith("bin/"):
            dest.chmod(0o755)
    version = plugin_version(root)
    if version:
        KIT_STAMP.parent.mkdir(parents=True, exist_ok=True)
        KIT_STAMP.write_text(version + "\n")


def tmux_kit(action, root):
    conf = user_tmux_conf()
    line = f"source-file {home_short(KIT_CONF)}"
    text = conf.read_text() if conf.is_file() else ""
    sourced = str(KIT_CONF) in text or home_short(KIT_CONF) in text
    if sourced and KIT_CONF.is_file():
        print(f"tmux-kit: installed ({conf} sources {KIT_CONF})")
        return
    new = text if sourced else text + ("\n" if text and not text.endswith("\n") else "") + f"\n{KIT_NOTE}\n{line}\n"
    if action == "diff":
        print("tmux-kit: not installed; install copies " + ", ".join(home_short(d) for d in KIT.values()))
        if sourced:
            print(f"{conf} already sources the kit")
        else:
            print(f"and adds this to {conf}:")
            sys.stdout.writelines(difflib.unified_diff(
                text.splitlines(True), new.splitlines(True), str(conf), f"{conf} (after)"))
        return
    copy_kit(root)
    for dest in KIT.values():
        print(f"copied: {dest}")
    if not sourced:
        if text:
            backup = conf.with_name(conf.name + ".flowkit-bak")
            backup.write_text(text)
            print(f"backup: {backup}")
        conf.parent.mkdir(parents=True, exist_ok=True)
        conf.write_text(new)
        print(f"added to {conf}: {line}")
    print(f"tmux-kit: installed; a running tmux picks it up with: tmux source-file {home_short(conf)}")


def launchctl(*args):
    """launchctl's exit code and what it said; (None, "") where there is none (anything but macOS).
    FLOWKIT_LAUNCHCTL names another program: the tests' recorder."""
    try:
        out = subprocess.run([os.environ.get("FLOWKIT_LAUNCHCTL") or "launchctl", *args], capture_output=True, text=True)
    except OSError:
        return None, ""
    return out.returncode, (out.stderr or out.stdout).strip()


def timer_plist():
    """The LaunchAgent, as bytes. launchd starts a job with almost no environment, so the PATH the
    setup ran with is written in (without folders that are gone, and without the plugin cache's:
    those carry a version and the tick finds the plugin itself), and CLAUDE_CONFIG_DIR,
    XDG_CONFIG_HOME and LANG where the setup's shell has them: the timer ticks the config folders
    the setup ran in. A variable the shell does not set is left out, never spelled out: Claude Code
    keeps another account file once CLAUDE_CONFIG_DIR is set."""
    folders = []
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if folder and folder not in folders and Path(folder).is_dir() and CLAUDE / "plugins" not in Path(folder).parents:
            folders.append(folder)
    env = {"PATH": os.pathsep.join(folders), **{k: os.environ[k] for k in TIMER_ENV if os.environ.get(k)}}
    return plistlib.dumps({
        "Label": TIMER, "ProgramArguments": [str(SHIMS["bin/flowkit-tick"]), "--timer"], "StartInterval": TIMER_EVERY,
        "RunAtLoad": True, "ProcessType": "Background", "EnvironmentVariables": env,
        "StandardOutPath": "/dev/null", "StandardErrorPath": "/dev/null"})


def timer(action, root):
    """Show, or install, the timer: the commands copied to ~/.local/bin (the tmux kit or not) and a
    LaunchAgent that runs `flowkit-tick --timer` every TIMER_EVERY seconds, loaded at once and at
    every login."""
    if not shutil.which(os.environ.get("FLOWKIT_LAUNCHCTL") or "launchctl"):
        print("timer: not on this system (launchd is macOS's): the tmux kit's status bar runs the tick, while a "
              "tmux client is attached")
        return
    new = timer_plist()
    old = TIMER_PLIST.read_bytes() if TIMER_PLIST.is_file() else b""
    domain = f"gui/{os.getuid()}"
    whole = old == new and all(dest.is_file() for dest in SHIMS.values())
    if whole and launchctl("print", f"{domain}/{TIMER}")[0] == 0:
        print(f"timer: installed ({TIMER_PLIST} runs {home_short(SHIMS['bin/flowkit-tick'])} every {TIMER_EVERY} seconds)")
        return
    if action == "diff":
        print(f"timer: {'written, not loaded' if whole else 'installed with other values' if old else 'not installed'}"
              "; install copies " + ", ".join(home_short(d) for d in SHIMS.values())
              + f" and {'changes' if old else 'writes'} {TIMER_PLIST}, which runs flowkit-tick every {TIMER_EVERY} "
              f"seconds for {CLAUDE} with the settings in {CONFIG}:")
        sys.stdout.writelines(difflib.unified_diff(
            old.decode(errors="replace").splitlines(True), new.decode().splitlines(True),
            str(TIMER_PLIST), f"{TIMER_PLIST} (after)"))
        return
    copy_kit(root, SHIMS)
    for dest in SHIMS.values():
        print(f"copied: {dest}")
    TIMER_PLIST.parent.mkdir(parents=True, exist_ok=True)
    TIMER_PLIST.write_bytes(new)
    print(f"written: {TIMER_PLIST}")
    launchctl("bootout", f"{domain}/{TIMER}")  # an older one, so the new values count; none is no error
    code, said = launchctl("bootstrap", domain, str(TIMER_PLIST))
    if code:
        fail(f"timer: launchctl could not load it ({said or code}); by hand: launchctl bootstrap {domain} {TIMER_PLIST}")
    print(f"timer: installed; flowkit-tick runs every {TIMER_EVERY} seconds, with tmux attached or not "
          f"(`{home_short(SHIMS['bin/flowkit-tick'])} status` says when it last ran)")


def setup_remove(dry):
    """Take away what the setups put on this computer outside the plugin: the timer, the commands in
    ~/.local/bin, the tmux kit's files and the line that sources them (a backup beside it). What the
    user set stays: handoff.conf, capacity.conf, a ladder, the status line."""
    did = "would remove" if dry else "removed"
    gone = 0
    if TIMER_PLIST.is_file():
        if not dry:
            launchctl("bootout", f"gui/{os.getuid()}/{TIMER}")
            TIMER_PLIST.unlink()
        print(f"{did}: {TIMER_PLIST} (the timer, unloaded)")
        gone += 1
    files = list(KIT.values()) + [KIT_STAMP]
    resolver = KIT["tmux/capacity-shim.py"]
    try:
        if "handoff@flowkit" not in resolver.read_text(errors="replace"):
            files.remove(resolver)  # a capacity.py of the user's own
    except OSError:
        pass
    for path in files:
        if path.is_file():
            if not dry:
                path.unlink()
            print(f"{did}: {path}")
            gone += 1
    conf = user_tmux_conf()
    text = conf.read_text() if conf.is_file() else ""
    new = re.sub(r"(?:^\n)?(?:^%s\n)?^source-file (?:%s|%s)[ \t]*\n?" % tuple(
        re.escape(s) for s in (KIT_NOTE, home_short(KIT_CONF), str(KIT_CONF))), "", text, flags=re.M)
    if new != text:
        if not dry:
            backup = conf.with_name(conf.name + ".flowkit-bak")
            backup.write_text(text)
            print(f"backup: {backup}")
            conf.write_text(new)
        print(f"{did}: the line in {conf} that sources the kit (a running tmux keeps the kit until it restarts)")
        gone += 1
    if str(KIT_CONF) in new or home_short(KIT_CONF) in new:
        print(f"left: {conf} still names {home_short(KIT_CONF)}; take that line out yourself")
    if not gone:
        print("setup: nothing to remove")
    stay = [str(p) for p in (SETTINGS_FILE, CONFIG / "capacity.conf", LADDER) if p.is_file()]
    if stay:
        print("kept, yours: " + ", ".join(stay))
    if str(STATUSLINE) in settings_text():
        print(f"kept: the status line ({STATUSLINE}); to undo it, take `statusLine` out of {SETTINGS}")


def prune_marks():
    """A session's hook state (`mark_file`) goes PRUNE_DAYS after its last prompt."""
    cutoff = time.time() - PRUNE_DAYS * 86400
    try:
        for f in (CLAUDE / "flowkit" / "context").iterdir():
            if f.suffix == ".json" and f.stat().st_mtime < cutoff:
                f.unlink()
    except OSError:
        pass


def prune_spawned():
    """Drop the copies flowkit-spawn keeps of each prompt from outside the store (and of each
    reporter prompt) once they are PRUNE_DAYS old. A saved prompt stays: a story file's launch
    line may still cat it."""
    cutoff = time.time() - PRUNE_DAYS * 86400
    gone = 0
    try:
        for f in SPAWNED.iterdir():
            if COPY.search(f.name) and f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                gone += 1
    except OSError:
        pass
    if gone:
        print(f"spawned: {gone} prompt {'copy' if gone == 1 else 'copies'} older than {PRUNE_DAYS} days removed")


def register(hook):
    """The session whose start this hook is registers itself in backend.py's file: the pane it
    runs in and what that pane's tab was opened for. One outside tmux registers nothing, and
    neither does one a session started from inside its own pane (`inside`: the processes above
    this one)."""
    try:
        sid = json.loads(hook).get("session_id")
    except (ValueError, AttributeError):
        return
    pid = os.environ.get("CLAUDE_PID", "")
    if not (isinstance(sid, str) and sid):
        return
    try:
        place = TMUX.locate(pid=int(pid)) if pid.isdigit() else TMUX.here()
        if place:
            above, at, inside = backend.parents(), int(pid) if pid.isdigit() else None, []
            while at in above and len(inside) < 8:
                at = above[at]
                inside.append(at)
            story, adhoc = TMUX.marked(place)
            backend.register(sid, TMUX.name, place.id, story, adhoc, int(pid) if pid.isdigit() else None, inside)
    except (OSError, subprocess.SubprocessError, ValueError):
        pass


def refresh(root):
    """Keep an installed status line, tmux kit and the commands the timer runs at the plugin's
    version, and prune the old prompt copies and hook state. The copies only go forward: a session
    on an older plugin leaves newer ones alone. Its output reaches context, so it says nothing
    unless something went."""
    prune_spawned()
    prune_marks()
    source = Path(root) / "scripts" / "statusline.py"
    try:
        if STATUSLINE.is_file() and STATUSLINE.read_bytes() != source.read_bytes():
            shutil.copyfile(source, STATUSLINE)
    except OSError:
        pass
    try:
        installed = kit_version(KIT_STAMP.read_text()) if KIT_STAMP.is_file() else None
        own = kit_version(plugin_version(root))
        if not (installed and own and installed > own):
            if KIT_CONF.is_file():
                copy_kit(root)
            elif TIMER_PLIST.is_file():
                copy_kit(root, SHIMS)
    except OSError:
        pass


def main(argv):
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "state" and len(args) == 2:
        state(*args)
    elif cmd == "pickup-doc" and len(args) == 3:
        pickup_doc(*args)
    elif cmd == "save-prompt" and (len(args) == 1 or (len(args) == 3 and args[1] == "--report-to")):
        save_prompt(args[0], *args[2:])
    elif cmd == "self" and not args:
        whoami()
    elif cmd == "record" and len(args) == 1:
        record(*args)
    elif cmd == "chain-end" and len(args) in (1, 2):
        chain_end(*args)
    elif cmd in ("away", "awake", "back") and (not args or (len(args) == 2 and args[0] == "--by")):
        {"away": away, "awake": awake, "back": back}[cmd](label(args[1]) if args else "unknown")
    elif cmd == "away-nudge" and args in ([], ["--wait"]):
        away_nudge(bool(args))
    elif cmd == "restamp" and not args:
        if AWAY.is_file():
            os.utime(AWAY)
            print(f"{away_status()} (re-stamped {time.strftime('%H:%M')}; clears with back or after {AWAY_HOURS} hours)")
        else:
            print("away: off")
    elif cmd == "question-gate" and not args:
        question_gate()
    elif cmd == "context" and not args:
        try:
            context_hook()
        except Exception:
            pass
    elif cmd == "remind" and args in ([], ["--dry-run"]):
        (remind_dry if args else remind)()
    elif cmd == "usage" and args in ([], ["--json"]):
        sys.exit(usage(*args))
    elif cmd == "wake" and len(args) == 2:
        try:
            wake(*args, patience=300)
        except Exception:
            pass
    elif cmd == "wake" and len(args) == 3 and args[0] == "--id":
        try:
            wake("", args[2], patience=300, sid=args[1])
        except Exception:
            pass
    elif cmd == "session":
        sys.exit(session_cmd(args))
    elif cmd == "reconnect" and (args in ([], ["--all"]) or (len(args) == 1 and SAFE.match(args[0]))):
        try:
            reconnect(args[0], patience=SETTLE) if args and args != ["--all"] else reconnect_pass(bool(args))
        except Exception:
            pass
    elif cmd in ("api-error", "api-ok", "retry-wait") and len(args) == (cmd == "retry-wait"):
        try:
            {"api-error": api_error_hook, "api-ok": api_ok_hook, "retry-wait": retry_wait}[cmd](*args)
        except Exception:
            pass
    elif cmd == "conf" and len(args) == 2:
        conf(*args)
    elif cmd == "settings" and len(args) >= 2 and args[0] in ("diff", "install"):
        user_settings(args[0], args[1:])
    elif cmd == "statusline" and len(args) == 2 and args[0] in ("diff", "install"):
        statusline(*args)
    elif cmd == "tmux" and len(args) == 2 and args[0] in ("diff", "install"):
        tmux_kit(*args)
    elif cmd == "timer" and len(args) == 2 and args[0] in ("diff", "install"):
        timer(*args)
    elif cmd == "setup" and args in (["remove"], ["remove", "--dry-run"]):
        setup_remove(len(args) == 2)
    elif cmd == "refresh" and len(args) == 1:
        register(sys.stdin.read())
        refresh(*args)
    elif cmd == "away-status" and not args:
        print(away_status())
    else:
        fail(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
