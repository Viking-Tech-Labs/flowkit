#!/usr/bin/env python3
"""Files behind the handoff skills: the repo's pick-up doc, the ladder file, the away flag,
saved prompts and story files. The skills call this with plain arguments; nothing here asks.

  handoff.py state <data-dir> <repo>             what a close needs to know, one line each
  handoff.py pickup-doc <data-dir> <repo> <doc>  remember the repo's pick-up doc
  handoff.py save-prompt <name>                  stdin -> ~/.claude/spawned/<name>.md
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
  handoff.py away | back | away-status           the ~/.claude/away flag (12 hours); `away` then nudges
                                                 the sessions parked on a question (bin/flowkit-away),
                                                 `back` lists the sessions waiting on the user
  handoff.py question-gate                       PreToolUse hook on AskUserQuestion: denied while away
  handoff.py context                             UserPromptSubmit and PostToolBatch hook: the context line
  handoff.py api-error | api-ok                  StopFailure and Stop hooks: the retry record and line, and
                                                 with autoretry=on the waiter (retry-wait <session id>)
  handoff.py usage [--json]                      the plan's usage left (bin/flowkit-usage)
  handoff.py statusline diff|install <root>      the status line that records context and cost
  handoff.py tmux diff|install <root>            the tmux kit and the line that sources it
  handoff.py refresh <root>                      update an installed status line and tmux kit, prune the
                                                 prompt copies older than 30 days (SessionStart)
"""
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOME = Path.home()
CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
AWAY = CLAUDE / "away"
AWAY_HOURS = 12
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
}
KIT_CONF = KIT["tmux/tmux.conf"]
AWAY_BIN = Path(__file__).resolve().parent.parent / "bin" / "flowkit-away"
USAGE_BIN = Path(__file__).resolve().parent.parent / "bin" / "flowkit-usage"
STATS = CLAUDE / "session-stats"
CTX_STEPS = (40, 50, 60)
CTX_CLEAR = 35  # a reading this low (a /compact, a fresh start) lets the steps fire again
CTX_WARM = 30  # requests before a session hears anything: a successor that starts high must not loop
AWAKE = CLAUDE / "awake"  # out but reachable: written by the user's own setup, read like the away flag
RETRY = CLAUDE / "flowkit" / "retry"  # <session id>.json while a session sits on an API error
RETRIES_LOG = CLAUDE / "flowkit" / "retries.log"  # retry lines of sessions outside a story
RETRY_KINDS = ("server_error", "overloaded")  # what the waiter continues; rate_limit is Claude Code's own
RETRY_STEPS = (2, 5, 10, 20, 30)  # minutes the waiter waits before each continue, then every 30
RETRY_HOURS = 12
SETTLE = 10  # seconds after the error before the transcript is read as the session's resting state
PROMPT_LINE = re.compile(r"^[│|\s]*[>❯]\s?(.*?)[\s│|]*$")
HEADING = "WHERE TO PICK UP"
NAMES = ("PLAN.md", "BUILD-PLAN.md", "HANDOFF.md", "ROADMAP.md", "docs/PLAN.md", "docs/plan.md", "docs/HANDOFF.md")
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
CHAIN_END = "mr-ready"  # chain_end= of handoff.conf when the key is absent
CHAIN_ENDS = {  # a chain's end: what its slices do about pushing, the MR and the merge; the last slice ends it
    "branch": "no slice pushes; the last slice leaves the branch committed and the user pushes",
    "draft-mr": "each slice pushes its green commits, the first push opens a Draft MR; the last slice reads the "
                "pipeline and leaves the MR in Draft",
    "mr-ready": "each slice pushes its green commits, the first push opens a Draft MR; the last slice takes it out "
                "of Draft, watches the pipeline to green, no merge",
    "merge": "each slice pushes its green commits, the first push opens a Draft MR; the last slice takes it out of "
             "Draft, sets auto-merge and watches until it is merged",
}


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def safe(name, what):
    if not SAFE.match(name):
        fail(f"{what} must be letters, digits, '.', '_' or '-': {name!r}")
    return name


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


def away_since():
    """The stamp in the away flag when it is younger than AWAY_HOURS, else None."""
    try:
        if time.time() - AWAY.stat().st_mtime > AWAY_HOURS * 3600:
            return None
        return AWAY.read_text().strip() or "set"
    except OSError:
        return None


def reach(*args):
    """Run bin/flowkit-away with its output going straight to the skill; a miss never fails
    the flag (the sessions are reached as a courtesy, the flag is the record)."""
    try:
        subprocess.run([sys.executable, str(AWAY_BIN), *args], timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"flowkit-away: {e}")


def question_gate():
    """PreToolUse on AskUserQuestion. While the away flag is set the question is denied, with the
    reason the session acts on; otherwise silent, and the question goes to the user."""
    try:
        json.load(sys.stdin)
    except ValueError:
        return
    since = away_since()
    if not since:
        return
    reason = (f"The user is away (since {since}, {AWAY}): questions are not asked. Take the recommended "
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


def context_hook():
    """UserPromptSubmit and PostToolBatch. A session cannot see its own context use; the status
    line writes it to session-stats/<id>.json, one message behind. Every prompt at 40% or more gets
    one line; mid-run, each of 40, 50 and 60% is told once, and nothing from a /handoff:close until
    the next prompt. Silent for subagents, reporters (*-report), a session's first CTX_WARM requests,
    and without a reading."""
    data = json.load(sys.stdin)
    sid = str(data.get("session_id") or "")
    if data.get("agent_id") or not SAFE.match(sid):
        return
    prompt = data.get("hook_event_name") == "UserPromptSubmit"
    mark = Path(tempfile.gettempdir()) / f"flowkit-context-{sid}.json"
    try:
        seen = json.loads(mark.read_text())
    except (OSError, ValueError):
        seen = {}
    told = dict(seen)
    if prompt:
        seen.pop("quiet", None)
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
    if line:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit" if prompt else "PostToolBatch", "additionalContext": line}}))


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
    since = away_since()
    print(f"away: since {since}" if since else "away: off")
    print(f"spawn-driver: {setting('driver', 'tmux')}")
    print(f"chain-end: {chain_end_conf()}")
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


def save_prompt(name):
    SPAWNED.mkdir(parents=True, exist_ok=True)
    path = SPAWNED / f"{safe(name, 'the session name')}.md"
    path.write_text(sys.stdin.read().rstrip("\n") + "\n")
    print(path)


def reachable():
    """Away, or the awake flag (out but reachable from a phone) younger than AWAY_HOURS."""
    try:
        awake = time.time() - AWAKE.stat().st_mtime <= AWAY_HOURS * 3600
    except OSError:
        awake = False
    return bool(away_since()) or awake


def session_file(pid, sid):
    """~/.claude/sessions/<pid>.json, only while it is still this session's: pids are reused."""
    try:
        data = json.loads((CLAUDE / "sessions" / f"{pid}.json").read_text())
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) and data.get("sessionId") == sid else {}


def retry_record(sid):
    try:
        return json.loads((RETRY / f"{sid}.json").read_text())
    except (OSError, ValueError):
        return None


def api_error_hook():
    """StopFailure: an API error ended the turn, so Stop (and its idle hold) did not fire. Writes
    the retry record that labels the tab `error`, logs one line, and, with autoretry=on while the
    user is away or awake, starts the waiter that continues the session."""
    data = json.load(sys.stdin)
    sid = str(data.get("session_id") or "")
    if data.get("agent_id") or not SAFE.match(sid):
        return
    kind = str(data.get("error") or "unknown")
    pid = os.environ.get("CLAUDE_PID", "")
    now = time.time()
    rec = retry_record(sid) or {"first": now, "attempts": 0}
    info = session_file(pid, sid)
    rec.update(session=sid, kind=kind, at=now, pid=int(pid) if pid.isdigit() else None,
               name=info.get("name") or sid[:8], background=info.get("kind") == "background",
               transcript=str(data.get("transcript_path") or ""))
    RETRY.mkdir(parents=True, exist_ok=True)
    (RETRY / f"{sid}.json").write_text(json.dumps(rec))
    details = " ".join(str(data.get("error_details") or "").split())[:160]
    entry = f"> retry {rec['name']} · {time.strftime('%Y-%m-%d %H:%M')} · {kind}{' · ' + details if details else ''}\n"
    story = ""
    try:
        if os.environ.get("TMUX_PANE"):
            story = tmux("show-options", "-wqv", "-t", os.environ["TMUX_PANE"], "@flowkit_story").stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    path = STORIES / f"{story}.md" if SAFE.match(story) else None
    with (path if path and path.is_file() else RETRIES_LOG).open("a") as f:
        f.write(entry)
    if setting("autoretry", "off") != "on" or not reachable() or not rec["pid"]:
        return
    if kind in RETRY_KINDS or (kind == "max_output_tokens" and rec["attempts"] == 0):
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


def session_pane(pid):
    """The tmux pane the live process `pid` runs in, if it is not in copy mode."""
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
            return pane if in_mode == "0" else None
        pid = parents.get(pid)
        if pid is None:
            return None
    return None


def draft(pane):
    """Text sitting in the pane's prompt box (the last line that starts with the prompt mark)."""
    lines = [l for l in tmux("capture-pane", "-p", "-t", pane).stdout.splitlines() if PROMPT_LINE.match(l)]
    return bool(lines) and bool(PROMPT_LINE.match(lines[-1]).group(1))


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
    # one tmux call, so nothing else typing into panes lands in between; the pause lets the TUI take
    # the text as typed before Enter, which it would otherwise read as part of a paste
    return tmux("send-keys", "-t", pane, "-l", text, ";", "run-shell", "sleep 0.5",
                ";", "send-keys", "-t", pane, "Enter").returncode == 0


def retry_wait(sid):
    """The waiter one StopFailure starts: locked on session id plus pid, the computer kept awake
    while it waits. It waits the next step of RETRY_STEPS (minutes, then every 30) and types one
    continue, which is its own probe: the next error starts a waiter that keeps the count, and the
    next Stop deletes the record. Gives up 12 hours after the first error."""
    rec = retry_record(sid)
    if not rec or not rec.get("pid"):
        return
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
            if not rec or rec["pid"] != pid or not alive(pid) or time.time() > rec["first"] + RETRY_HOURS * 3600:
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


def api_ok_hook():
    """Stop: the turn ended cleanly, so the session's retry record goes."""
    sid = str(json.load(sys.stdin).get("session_id") or "")
    if SAFE.match(sid):
        (RETRY / f"{sid}.json").unlink(missing_ok=True)


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


def copy_kit(root):
    for rel, dest in KIT.items():
        source = Path(root) / rel
        if not dest.is_file() or dest.read_bytes() != source.read_bytes():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
        if rel.startswith("bin/"):
            dest.chmod(0o755)


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
    copies. Its output reaches context, so it says nothing unless something went."""
    prune_spawned()
    source = Path(root) / "scripts" / "statusline.py"
    try:
        if STATUSLINE.is_file() and STATUSLINE.read_bytes() != source.read_bytes():
            shutil.copyfile(source, STATUSLINE)
    except OSError:
        pass
    try:
        if KIT_CONF.is_file():
            copy_kit(root)
    except OSError:
        pass


def main(argv):
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "state" and len(args) == 2:
        state(*args)
    elif cmd == "pickup-doc" and len(args) == 3:
        pickup_doc(*args)
    elif cmd == "save-prompt" and len(args) == 1:
        save_prompt(*args)
    elif cmd == "record" and len(args) == 1:
        record(*args)
    elif cmd == "chain-end" and len(args) in (1, 2):
        chain_end(*args)
    elif cmd == "away" and not args:
        CLAUDE.mkdir(parents=True, exist_ok=True)
        AWAY.write_text(time.strftime("%Y-%m-%d %H:%M") + "\n")
        print(f"away: since {AWAY.read_text().strip()} (clears with back or after {AWAY_HOURS} hours)")
        sys.stdout.flush()
        usage()
        reach("nudge")
    elif cmd == "back" and not args:
        AWAY.unlink(missing_ok=True)
        print("away: off")
        sys.stdout.flush()
        reach("waiting")
    elif cmd == "question-gate" and not args:
        question_gate()
    elif cmd == "context" and not args:
        try:
            context_hook()
        except Exception:
            pass
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
        since = away_since()
        print(f"away: since {since}" if since else "away: off")
    else:
        fail(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
