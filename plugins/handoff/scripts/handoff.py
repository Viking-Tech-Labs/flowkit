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
                                                 last call is typed into every idle coordinator now; a flag
                                                 already set stays as it is
  handoff.py back [--by <session>]               clear the flag, then list the sessions waiting on the user
  handoff.py away-status                         away: since <stamp> (in effect), from <stamp> (set, not
                                                 yet in effect) or off
  handoff.py away-nudge                          the tick's: once the flag is in effect, nudge the sessions
                                                 parked on a question (bin/flowkit-away), once per flag
  handoff.py restamp                             an unattended close: the flag's 12 hours start again
                                                 while it exists (no nudge, no usage), so a chain
                                                 longer than that stays unattended
  handoff.py question-gate                       PreToolUse hook on AskUserQuestion: denied while the flag
                                                 is in effect, unless the user typed to this session in
                                                 the last 5 minutes
  handoff.py context                             UserPromptSubmit, PostToolBatch and PostToolUse (on
                                                 AskUserQuestion) hook: the context line, the away flag's
                                                 set and clear once each, the stamp of a prompt the user
                                                 typed or a question they answered
  handoff.py api-error | api-ok                  StopFailure and Stop hooks: the retry record and line, and
                                                 the waiter (retry-wait <session id>): after a usage limit
                                                 unless limitresume=off, after an API error with autoretry=on
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
  handoff.py refresh <root>                      update an installed status line and tmux kit, prune the
                                                 prompt copies older than 30 days (SessionStart)
"""
import contextlib
import difflib
import fcntl
import json
import os
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
AWAY_NUDGED = CLAUDE / "flowkit" / "away-nudged"  # the flag the tick's nudge ran for
AWAY_CLEARED = CLAUDE / "flowkit" / "away-cleared"  # who cleared the flag last, when, and which flag it was
PRESENT = 5  # minutes after the user typed to a session (or answered its question) in which it may still ask
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
KIT = {  # the tmux kit: where each file of the plugin is copied, so a tmux.conf never points at a version
    "tmux/tmux.conf": CONFIG / "tmux.conf",
    "tmux/cheatsheet.md": CONFIG / "tmux-cheatsheet.md",
    "bin/flowkit-tmux": HOME / ".local" / "bin" / "flowkit-tmux",
    "bin/flowkit-away": HOME / ".local" / "bin" / "flowkit-away",
    "bin/flowkit-usage": HOME / ".local" / "bin" / "flowkit-usage",
    "tmux/capacity-shim.py": HOME / ".local" / "scripts" / "capacity.py",  # where a 0.8.0 tick looks
}
KIT_CONF = KIT["tmux/tmux.conf"]
KIT_STAMP = CONFIG / "kit-version"  # the plugin version the kit was copied from; refresh never goes back
AWAY_BIN = Path(__file__).resolve().parent.parent / "bin" / "flowkit-away"
USAGE_BIN = Path(__file__).resolve().parent.parent / "bin" / "flowkit-usage"
STATS = CLAUDE / "session-stats"
CTX_STEPS = (40, 50, 60)
CTX_CLEAR = 35  # a reading this low (a /compact, a fresh start) lets the steps fire again
CTX_WARM = 30  # requests before a session hears anything: a successor that starts high must not loop
AWAKE = CLAUDE / "awake"  # out but reachable: written by the user's own setup, read like the away flag
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
REMIND = CLAUDE / "flowkit" / "remind.json"  # what the reminders have said: {once: {key: when}, digest: {at, keys}}
REMIND_IDLE = 10  # minutes without keyboard or mouse input after which the user is not at the computer
REMIND_EVERY = 120  # minutes between two digests
REMIND_DIR = CLAUDE / "flowkit" / "remind"  # each reminder's text as <stamp>.md; remind-latest.md beside the folder
REMIND_WAIT = 90  # seconds a rung reporter has to show its push in its transcript before the next one is rung
REMIND_RING = ("reminder: from flowkit's tick, not the user. Send the user one push notification now with the "
               "PushNotification tool (load it through ToolSearch if it is not loaded), its message word for word "
               'everything after "Message: "; then end the turn with "reminder sent" and nothing else. The full '
               "list: {path}. Message: {push}")
PARKED = CLAUDE / "flowkit" / "parked.json"  # flowkit-tmux's: the panes parked at a start-up prompt
PARKED_FRESH = 300  # seconds after which that file says nothing: the tick that writes it has stopped
PROMPT_LINE = re.compile(r"^[│|\s]*[>❯]\s?(.*?)[\s│|]*$")
DIALOG = re.compile(r"enter to (?:confirm|select|continue)|esc to (?!interrupt)\w+|^\s*(?:[❯>]\s*)?\d+\.\s", re.I | re.M)
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
    effect (the tick runs `away-nudge`). A flag already set stays as it is, and nudges again once in
    effect."""
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
    elif flag["at"] <= time.time():
        reach("nudge")


def back(by):
    """Clear the away flag, noting who and when (the context hook tells every session), then list
    the sessions waiting on the user."""
    try:
        text = AWAY.read_text()
        AWAY.unlink()
        AWAY_CLEARED.parent.mkdir(parents=True, exist_ok=True)
        AWAY_CLEARED.write_text(json.dumps({"by": by, "at": time.strftime("%Y-%m-%d %H:%M"), "flag": text}))
    except OSError:
        pass
    print("away: off")
    sys.stdout.flush()
    reach("waiting")


def last_call(by, stamp):
    """`away`'s last call: one line typed into every idle coordinator in a tmux pane (a name on a
    coordination board's `Coordinator:` line, or with `coord` at its start or after a `-`), only into
    a free prompt box; never into a reporter, the setter, or a session that is busy or waits."""
    try:
        rows = [r for r in agents_json() if isinstance(r, dict)]
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
        if mode(row) != "idle":
            print(f"no last call {name}: {mode(row)}")
            continue
        pane, why = type_into(row, text)
        print(f"no last call {name}: {why}" if why else f"last call {name} (tmux {pane})")


def away_nudge():
    """The tick's, while a flag waits for it: once the flag is in effect, the sessions parked on a
    question get the nudge, once per flag (an expired flag is marked done without one)."""
    try:
        text = AWAY.read_text()
    except OSError:
        return
    flag = away_flag()
    if flag and flag["at"] > time.time():
        return
    try:
        if AWAY_NUDGED.read_text() == text:
            return
    except OSError:
        pass
    AWAY_NUDGED.parent.mkdir(parents=True, exist_ok=True)
    AWAY_NUDGED.write_text(text)
    if flag:
        reach("nudge")


def mark_file(sid):
    """A session's hook state: the context steps, whether it had a prompt, when the user last typed
    to it, the away flag it last heard of."""
    return Path(tempfile.gettempdir()) / f"flowkit-context-{sid}.json"


def typed_by_user(prompt, first):
    """A prompt the user typed, at the computer or from the phone: not the session's first (a
    launch's brief), not a wrapper (a task's notice, a /command's lines, another session's message)
    and not a line flowkit types (a coordinator's `wake: ` of a stopped worker is one)."""
    prompt = prompt.lstrip()
    return not first and not prompt.startswith(WRAPPERS) and not FLOWKIT_LINE.match(prompt)


def present(sid):
    """The user typed to this session, or answered its question, in the last PRESENT minutes."""
    try:
        return time.time() - json.loads(mark_file(sid).read_text()).get("typed", 0) < PRESENT * 60
    except (OSError, ValueError, AttributeError, TypeError):
        return False


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
    mark = mark_file(sid)
    try:
        seen = json.loads(mark.read_text())
    except (OSError, ValueError):
        seen = {}
    told = dict(seen)
    if data.get("hook_event_name") == "PostToolUse":  # an answered question: the user is there
        mark.write_text(json.dumps({**seen, "typed": time.time()}))
        return
    if prompt:
        seen.pop("quiet", None)
        if typed_by_user(str(data.get("prompt") or ""), not seen.get("prompted")):
            seen["typed"] = time.time()
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
        mark.write_text(json.dumps(seen))
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
            rows = agents_json()
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
        if os.environ.get("TMUX_PANE"):
            story = tmux("show-options", "-wqv", "-t", os.environ["TMUX_PANE"], "@flowkit_story").stdout.strip()
            if kind == "rate_limit":
                screen = bottom(os.environ["TMUX_PANE"])
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


def pane_of(pid):
    """(pane id, in copy mode) of the tmux pane the live process `pid` runs in, or None."""
    panes = {}
    for line in tmux("list-panes", "-a", "-F", "#{pane_pid} #{pane_id} #{pane_in_mode}").stdout.splitlines():
        p, pane, in_mode = (line.split() + ["", "", ""])[:3]
        if p.isdigit():
            panes[int(p)] = (pane, in_mode)
    ps = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=10).stdout
    parents = {int(a): int(b) for a, b in (row.split() for row in ps.splitlines() if len(row.split()) == 2)}
    for _ in range(4):
        if pid in panes:
            pane, in_mode = panes[pid]
            return pane, in_mode != "0"
        pid = parents.get(pid)
        if pid is None:
            return None
    return None


def session_pane(pid):
    """The tmux pane the live process `pid` runs in, if it is not in copy mode."""
    found = pane_of(pid)
    return found[0] if found and not found[1] else None


def draft(pane):
    """Text sitting in the pane's prompt box (the last line that starts with the prompt mark)."""
    lines = [l for l in tmux("capture-pane", "-p", "-t", pane).stdout.splitlines() if PROMPT_LINE.match(l)]
    return bool(lines) and bool(PROMPT_LINE.match(lines[-1]).group(1))


def bottom(pane):
    """The bottom 8 rows of what a pane shows, as a list."""
    rows = tmux("capture-pane", "-p", "-t", pane).stdout.splitlines()
    while rows and not rows[-1].strip():
        rows.pop()
    return rows[-8:]


def parked(pane):
    """Why the pane's prompt box cannot take a typed line, "" when it can: there is none on screen,
    dialog rows sit below it (a menu's numbered options, its Enter or Esc hint), or it holds a
    draft. Only rows below the last prompt-box line count, so a dialog quoted in the conversation
    above it never does."""
    rows = tmux("capture-pane", "-p", "-t", pane).stdout.splitlines()
    last = max((i for i, row in enumerate(rows) if PROMPT_LINE.match(row)), default=None)
    if last is None:
        return "no prompt box"
    if DIALOG.search("\n".join(rows[last + 1:])):
        return "dialog"
    return "draft" if PROMPT_LINE.match(rows[last]).group(1) else ""


def type_line(pane, text):
    """Type `text` into the pane and press Enter. One tmux call, so nothing else typing into panes
    lands in between; the pause lets the TUI take the text as typed before Enter, which it would
    otherwise read as part of a paste."""
    return tmux("send-keys", "-t", pane, "-l", text, ";", "run-shell", "sleep 0.5",
                ";", "send-keys", "-t", pane, "Enter").returncode == 0


def type_into(row, text):
    """Type a line into the tmux pane of the session a `claude agents` row names, only into a free
    prompt box: (pane, "") once typed, else (None, why not). No tmux on the machine, or one that
    hangs, is a why like the others."""
    try:
        found = pane_of(row["pid"]) if row.get("kind") == "interactive" and row.get("pid") else None
        why = "not in a tmux pane" if found is None else "copy mode" if found[1] else parked(found[0])
        if not why and not type_line(found[0], text):
            why = "tmux did not take the line"
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"tmux or ps did not answer ({type(e).__name__})"
    return (None, why) if why else (found[0], "")


def continue_session(rec, text):
    """Type `text` as the session's next prompt, or resume a background one with it, once every
    check passes: still idle, the pane is the live process's, not in copy mode, no draft."""
    if not session_file(rec["pid"], rec["session"]):
        return False
    if rec.get("background"):
        row = next((a for a in agents_json() if isinstance(a, dict) and a.get("sessionId") == rec["session"]), None)
        if row is None or mode(row) != "idle":
            return False
        wake("", text, sid=rec["session"])
        return True
    if session_file(rec["pid"], rec["session"]).get("status") != "idle":
        return False
    pane = session_pane(rec["pid"])
    if pane is None or draft(pane):
        return False
    return type_line(pane, text)


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
    """pane_of for a limit record's session; None too when there is no tmux to ask."""
    try:
        return pane_of(rec["pid"])
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
    rest = {"status": session_file(rec["pid"], sid).get("status"), "screen": bottom(found[0]) if found else []}
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
    if found[1]:
        return "copy mode"
    return parked(found[0]) or ("" if type_line(found[0], text) else "not typed")


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
    to, and starts it over (limit_switch)."""
    lock = limit_lock(sid)
    if lock is None:
        return
    unit = float(os.environ.get("FLOWKIT_RETRY_MINUTE") or 60)
    nap = max(min(5.0, unit / 12), 0.05)
    awake, at, due, reset, look = None, None, None, None, 0
    try:
        while True:
            rec = retry_record(sid)
            if not rec or rec.get("kind") != "rate_limit" or not rec.get("pid") or not alive(rec["pid"]) or \
                    not session_file(rec["pid"], sid):
                return
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
                    "continued_for": reset or time.time(), "handled": at, "state": "continued"})
            elif why == "moved":
                limit_mark(sid, handled=at, state="moved")
            elif why == "unreachable":
                limit_mark(sid, "unreachable", f"{head}it does not run in a tmux pane; it waits for you",
                           handled=at, state="unreachable")
            else:  # at work, copy mode, a draft, a dialog: the next slot
                if after_switch(rec, unit) and time.time() < rec["switched"] + SWITCH_QUIET * unit:
                    limit_mark(sid, state=why)
                    due = time.time() + unit / 2  # the account change itself may be what is in the way: soon again
                    continue
                limit_mark(sid, "dialog" if why == "dialog" else None,
                           f"{head}a dialog is open below its prompt box; looked at again every "
                           f"{RETRY_STEPS[-1]} minutes", state=why)
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


def reconnect(sid, patience=0):
    """One session's Remote Control after the account of its login changed. Claude Code drops the
    link then, says so in the transcript and brings it back only when asked, so `/remote-control`
    is typed for the session: once per notice, and only while it shows no link, is idle (not at
    work, not on a question) and its prompt box is on screen, empty, with no dialog below it. Until
    then it stays owed (<sid>.owed), for the next turn end and the tick. The link has REMOTE_WAIT
    seconds to show; when it does not, a dialog the command left open is closed with Esc and one
    line says so. A session whose link stands gets nothing, nor does one that never said it lost
    it to an account change; no session is ever restarted for this. Returns what was typed, or ""."""
    info = next((i for i in sessions() if i["sessionId"] == sid), None)
    mark, owed = REMOTE / f"{sid}.json", REMOTE / f"{sid}.owed"
    if info is None or info.get("kind") != "interactive":
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
        if not notice or any(r.get("subtype") == "bridge_status" and (stamp(r) or 0) > notice for r in rows):
            owed.unlink(missing_ok=True)  # nothing new since the last one handled, or the link came back after it
            return ""
        deadline = time.time() + patience * unit
        while fresh().get("status") != "idle" and time.time() < deadline:
            time.sleep(0.2)
        try:
            found = pane_of(pid) if fresh().get("status") == "idle" else None
            pane = found[0] if found and not found[1] and not parked(found[0]) else None
        except (OSError, subprocess.SubprocessError):
            pane = None
        if pane is None or not type_line(pane, "/remote-control"):
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
                tmux("send-keys", "-t", pane, "Escape")
            story = tmux("show-options", "-wqv", "-t", pane, "@flowkit_story").stdout.strip()
            retry_log({"story": story if SAFE.match(story) else ""},
                      f"> remote {info.get('name') or sid[:8]} · {time.strftime('%Y-%m-%d %H:%M')} · Remote Control did not "
                      f"come back after the account changed{': the dialog it left open was closed' if left else ''}; "
                      "type /remote-control in the session")
        mark.write_text(json.dumps({"notice": notice, "at": time.time(), "state": "back" if back else "failed"}))
        return "/remote-control"


def reconnect_pass(every):
    """The tick's pass: every session that is owed its Remote Control, or with `every` each live
    interactive one that shows no link (its transcript says whether it lost one to an account
    change), at the same time; then the files of sessions that are gone go."""
    live = {i["sessionId"]: i for i in sessions()}
    REMOTE.mkdir(parents=True, exist_ok=True)
    for path in REMOTE.iterdir():
        if path.suffix in (".json", ".owed", ".lock") and path.stem not in live:
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


def tmux(*args):
    sock = os.environ.get("FLOWKIT_TMUX_SOCKET")
    return subprocess.run(
        ["tmux", *(["-L", sock] if sock else []), *args], capture_output=True, text=True, timeout=10
    )


def reporter_pane(agents, panes, parents, name):
    """The tmux pane id and status of the live interactive session called `name`, found through
    its parent processes (a pane's pid is the shell that started it)."""
    for a in agents:
        if a.get("kind") == "interactive" and a.get("name") == name and a.get("pid"):
            pid = a["pid"]
            for _ in range(4):
                if pid in panes:
                    return panes[pid], a.get("status")
                pid = parents.get(pid)
                if pid is None:
                    break
    return None


def agents_json(*args):
    return json.loads(
        subprocess.run(["claude", "agents", "--json", *args], capture_output=True, text=True, timeout=15).stdout
    )


def bg_reporter(slug, sid=None):
    """The story's background reporter, or with sid the background session with that id."""
    rows = [a for a in agents_json("--all") if isinstance(a, dict) and a.get("kind") == "background"
            and (a.get("sessionId") == sid if sid else a.get("name") == f"{slug}-report")]
    return max(rows, key=lambda a: a.get("startedAt") or 0) if rows else None


def mode(agent):
    """busy, waiting or idle. A background entry's `status` is there while its process runs and
    wins over `state`, which can still say working after the turn ended."""
    status = agent.get("status")
    if status is None:
        status = {"working": "busy", "blocked": "waiting"}.get(agent.get("state"), "idle")
    return status


def wake(slug, name, patience=0, sid=None):
    """Ring a background reporter (with sid: resume that background session with `name` as the prompt). `claude --bg --resume` hands a stopped session the prompt under
    its own id; a live one would get a copy instead, so a live one is stopped first. Never while it
    waits on a prompt; while it is mid-turn, a process of its own waits up to five minutes, so the
    close does not."""
    reporter = bg_reporter(slug, sid)
    deadline = time.time() + patience
    while reporter is not None and mode(reporter) == "busy":
        if time.time() >= deadline:
            if patience == 0:
                again = ["--id", sid, name] if sid else [slug, name]
                subprocess.Popen([sys.executable, __file__, "wake", *again], start_new_session=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        time.sleep(10)
        reporter = bg_reporter(slug, sid)
    if reporter is None or mode(reporter) == "waiting":
        return
    if reporter.get("pid"):
        subprocess.run(["claude", "stop", reporter["id"]], capture_output=True, text=True, timeout=30)
        for _ in range(20):  # `stop` returns before the process is gone
            time.sleep(0.5)
            if not (bg_reporter(slug, sid) or {}).get("pid"):
                break
        else:
            return
    subprocess.run(
        ["claude", "--bg", "--resume", reporter["sessionId"], name if sid else f"closed {name}"],
        cwd=reporter.get("cwd") or None, capture_output=True, text=True, timeout=60,
    )


def ring(slug, name):
    """Tell the story's reporter `closed <name>`: a background one is woken with it as its prompt,
    a tmux one gets it typed into its pane when it is live and not waiting on a prompt. The file is
    the record; this is only the doorbell, so a miss never fails the close. Returns what happened:
    rung, background (woken), waiting (found, but at a prompt) or none (no reporter runs)."""
    if not shutil.which("claude"):
        return "none"
    try:
        agents = agents_json("--all")
        if not isinstance(agents, list):
            return "none"
        if any(isinstance(a, dict) and a.get("kind") == "background" and a.get("name") == f"{slug}-report"
               for a in agents):
            wake(slug, name)
            return "background"
        if not shutil.which("tmux"):
            return "none"
        panes = {}
        for line in tmux("list-panes", "-a", "-F", "#{pane_pid} #{pane_id}").stdout.splitlines():
            pid, pane = line.split()
            panes[int(pid)] = pane
        ps = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=10).stdout
        parents = {int(a): int(b) for a, b in (row.split() for row in ps.splitlines() if row.strip())}
        found = reporter_pane(agents, panes, parents, f"{slug}-report")
        if found is None:
            return "none"
        if found[1] == "waiting":
            return "waiting"
        # one tmux call, as continue_session: nothing else typing into panes lands in between
        tmux("send-keys", "-t", found[0], "C-u", ";", "send-keys", "-t", found[0], "-l", f"closed {name}",
             ";", "run-shell", "sleep 0.5", ";", "send-keys", "-t", found[0], "Enter")
        return "rung"
    except Exception:  # the record is already written; a doorbell never fails the close
        return "none"


def idle_seconds(now):
    """Seconds since the user's last keyboard or mouse input: macOS's HID idle time, elsewhere the
    newest activity of a tmux client; None when neither answers."""
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=10).stdout
        found = re.search(r'"HIDIdleTime" = (\d+)', out)
        if found:
            return int(found[1]) / 1e9
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        seen = [int(x) for x in tmux("list-clients", "-F", "#{client_activity}").stdout.split() if x.isdigit()]
    except (OSError, subprocess.TimeoutExpired):
        seen = []
    return max(0, now - max(seen)) if seen else None


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
        rows = agents_json()
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
    """The push: one line under 200 characters, the first thing that waits and how many more."""
    more = f" (+{len(lines) - 1} more in remind-latest.md)" if len(lines) > 1 else ""
    first = " ".join(lines[0].split())
    room = 195 - len(heading) - len(more)
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
    REMIND_WAIT seconds for the answer to the push it sends. (sent, what happened); a push the tool
    did not send ("Not sent — this terminal is active", an error) is not sent."""
    at = time.time()
    why = type_into(row, line)[1]
    if why:
        return False, why
    while True:
        result = pushed(row["sessionId"], at)
        if result is not None:
            return not result.startswith(("Not sent", "error: ")), result
        if time.time() - at >= REMIND_WAIT:
            return False, f"no push in {REMIND_WAIT} s"
        time.sleep(REMIND_WAIT / 18)


def remind_deliver(push, path):
    """Send the push through the first that carries it: the oldest idle reporter in a tmux pane whose
    story has no final report after its last close, then the next one; else the `notify` command of
    handoff.conf, the push on its stdin; else a macOS notification. Returns what carried it, or ""."""
    line = REMIND_RING.format(path=home_short(path), push=push)
    try:
        rows = [r for r in agents_json() if isinstance(r, dict)]
    except (OSError, subprocess.TimeoutExpired, ValueError):
        rows = []
    for row in sorted(rows, key=lambda r: r.get("startedAt") or 0):
        name = str(row.get("name") or "")
        if not name.endswith("-report") or row.get("kind") != "interactive" or mode(row) != "idle" \
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
    at the computer; kept as said only once a push arrived, so a pass whose push did not arrive is
    said again at the next. One pass at a time: a delivery can take minutes."""
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
        heading, lines, state = remind_pass(found, stale, remind_state(), now)
        if not lines:
            remind_mark(state)
            print("remind: nothing to say")
            return
        path = remind_file(heading, lines, now)
        how = remind_deliver(remind_push(heading, lines), path)
        if how:
            remind_mark(state)
        print(f"remind: sent through {how} ({home_short(path)})" if how else
              f"remind: not delivered, said again at the next pass ({home_short(path)})")


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


def copy_kit(root):
    for rel, dest in KIT.items():
        source = Path(root) / rel
        if not dest.is_file() or dest.read_bytes() != source.read_bytes():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
        if rel.startswith("bin/"):
            dest.chmod(0o755)
    version = plugin_version(root)
    if version:
        KIT_STAMP.write_text(version + "\n")


def tmux_kit(action, root):
    conf = user_tmux_conf()
    line = f"source-file {home_short(KIT_CONF)}"
    text = conf.read_text() if conf.is_file() else ""
    sourced = str(KIT_CONF) in text or home_short(KIT_CONF) in text
    if sourced and KIT_CONF.is_file():
        print(f"tmux-kit: installed ({conf} sources {KIT_CONF})")
        return
    new = text if sourced else text + ("\n" if text and not text.endswith("\n") else "") + \
        f"\n# flowkit tmux kit (handoff plugin); your own settings go below this line\n{line}\n"
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


def refresh(root):
    """Keep an installed status line and tmux kit at the plugin's version, and prune the old prompt
    copies. The kit only goes forward: a session on an older plugin leaves a newer kit alone. Its
    output reaches context, so it says nothing unless something went."""
    prune_spawned()
    source = Path(root) / "scripts" / "statusline.py"
    try:
        if STATUSLINE.is_file() and STATUSLINE.read_bytes() != source.read_bytes():
            shutil.copyfile(source, STATUSLINE)
    except OSError:
        pass
    try:
        installed = kit_version(KIT_STAMP.read_text()) if KIT_STAMP.is_file() else None
        own = kit_version(plugin_version(root))
        if KIT_CONF.is_file() and not (installed and own and installed > own):
            copy_kit(root)
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
    elif cmd in ("away", "back") and (not args or (len(args) == 2 and args[0] == "--by")):
        (away if cmd == "away" else back)(label(args[1]) if args else "unknown")
    elif cmd == "away-nudge" and not args:
        away_nudge()
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
    elif cmd == "refresh" and len(args) == 1:
        sys.stdin.read()
        refresh(*args)
    elif cmd == "away-status" and not args:
        print(away_status())
    else:
        fail(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
