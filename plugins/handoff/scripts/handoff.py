#!/usr/bin/env python3
"""Files behind the handoff skills: the repo's pick-up doc, the ladder file, the away flag,
saved prompts and story files. The skills call this with plain arguments; nothing here asks.

  handoff.py state <data-dir> <repo>             what a close needs to know, one line each
  handoff.py pickup-doc <data-dir> <repo> <doc>  remember the repo's pick-up doc
  handoff.py save-prompt <name>                  stdin -> ~/.claude/spawned/<name>.md
  handoff.py record <story>                      stdin appended to ~/.claude/stories/<slug>.md (the
                                                 story slugified as flowkit-spawn does), then the
                                                 story's reporter is told `closed <name>`
  handoff.py wake <slug> <name>                  ring a busy background reporter once it is idle (5 min)
  handoff.py conf <key> <value>                  set a key of ~/.config/flowkit/handoff.conf
  handoff.py settings diff|install <key=json>…   values for the user's settings.json (a.b nests)
  handoff.py away | back | away-status           the ~/.claude/away flag (12 hours); `away` then nudges
                                                 the sessions parked on a question (bin/flowkit-away),
                                                 `back` lists the sessions waiting on the user
  handoff.py question-gate                       PreToolUse hook on AskUserQuestion: denied while away
  handoff.py statusline diff|install <root>      the status line that records context and cost
  handoff.py tmux diff|install <root>            the tmux kit and the line that sources it
  handoff.py refresh <root>                      update an installed status line and tmux kit (SessionStart)
"""
import difflib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
AWAY = CLAUDE / "away"
AWAY_HOURS = 12
SPAWNED = CLAUDE / "spawned"
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
}
KIT_CONF = KIT["tmux/tmux.conf"]
AWAY_BIN = Path(__file__).resolve().parent.parent / "bin" / "flowkit-away"
HEADING = "WHERE TO PICK UP"
NAMES = ("PLAN.md", "BUILD-PLAN.md", "HANDOFF.md", "ROADMAP.md", "docs/PLAN.md", "docs/plan.md", "docs/HANDOFF.md")
SAFE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


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


def bg_reporter(slug):
    rows = [a for a in agents_json("--all") if isinstance(a, dict)
            and a.get("kind") == "background" and a.get("name") == f"{slug}-report"]
    return max(rows, key=lambda a: a.get("startedAt") or 0) if rows else None


def mode(agent):
    """busy, waiting or idle. A background entry's `status` is there while its process runs and
    wins over `state`, which can still say working after the turn ended."""
    status = agent.get("status")
    if status is None:
        status = {"working": "busy", "blocked": "waiting"}.get(agent.get("state"), "idle")
    return status


def wake(slug, name, patience=0):
    """Ring a background reporter. `claude --bg --resume` hands a stopped session the prompt under
    its own id; a live one would get a copy instead, so a live one is stopped first. Never while it
    waits on a prompt; while it is mid-turn, a process of its own waits up to five minutes, so the
    close does not."""
    reporter = bg_reporter(slug)
    deadline = time.time() + patience
    while reporter is not None and mode(reporter) == "busy":
        if time.time() >= deadline:
            if patience == 0:
                subprocess.Popen([sys.executable, __file__, "wake", slug, name], start_new_session=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        time.sleep(10)
        reporter = bg_reporter(slug)
    if reporter is None or mode(reporter) == "waiting":
        return
    if reporter.get("pid"):
        subprocess.run(["claude", "stop", reporter["id"]], capture_output=True, text=True, timeout=30)
        for _ in range(20):  # `stop` returns before the process is gone
            time.sleep(0.5)
            if not (bg_reporter(slug) or {}).get("pid"):
                break
        else:
            return
    subprocess.run(
        ["claude", "--bg", "--resume", reporter["sessionId"], f"closed {name}"],
        cwd=reporter.get("cwd") or None, capture_output=True, text=True, timeout=60,
    )


def ring(slug, name):
    """Tell the story's reporter `closed <name>`: a background one is woken with it as its prompt,
    a tmux one gets it typed into its pane when it is live and not waiting on a prompt. The file is
    the record; this is only the doorbell, so a miss is silent."""
    if not shutil.which("claude"):
        return
    try:
        agents = agents_json("--all")
        if not isinstance(agents, list):
            return
        if any(isinstance(a, dict) and a.get("kind") == "background" and a.get("name") == f"{slug}-report"
               for a in agents):
            wake(slug, name)
            return
        if not shutil.which("tmux"):
            return
        panes = {}
        for line in tmux("list-panes", "-a", "-F", "#{pane_pid} #{pane_id}").stdout.splitlines():
            pid, pane = line.split()
            panes[int(pid)] = pane
        ps = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=10).stdout
        parents = {int(a): int(b) for a, b in (row.split() for row in ps.splitlines() if row.strip())}
        found = reporter_pane(agents, panes, parents, f"{slug}-report")
        if found is None or found[1] == "waiting":
            return
        tmux("send-keys", "-t", found[0], "C-u")
        tmux("send-keys", "-t", found[0], "-l", f"closed {name}")
        tmux("send-keys", "-t", found[0], "Enter")
    except Exception:  # the record is already written; a doorbell never fails the close
        return


def record(story):
    slug = slugify(story)
    STORIES.mkdir(parents=True, exist_ok=True)
    path = STORIES / f"{safe(slug, 'the story slug')}.md"
    text = sys.stdin.read().strip("\n")
    if not text:
        fail("nothing to record: the close goes on stdin")
    head = "" if path.exists() else f"# {slug}\n"
    with path.open("a") as f:
        f.write(f"{head}\n{text}\n")
    print(path)
    section = re.match(r"## (.+?) · \d{4}-\d{2}-\d{2} \d{2}:\d{2}\s*$", text.splitlines()[0])
    if section and section.group(1) != "Final report":
        ring(slug, section.group(1))


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


def refresh(root):
    """Keep an installed status line and tmux kit at the plugin's version. Silent: its output
    reaches context."""
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
    elif cmd == "away" and not args:
        CLAUDE.mkdir(parents=True, exist_ok=True)
        AWAY.write_text(time.strftime("%Y-%m-%d %H:%M") + "\n")
        print(f"away: since {AWAY.read_text().strip()} (clears with back or after {AWAY_HOURS} hours)")
        sys.stdout.flush()
        reach("nudge")
    elif cmd == "back" and not args:
        AWAY.unlink(missing_ok=True)
        print("away: off")
        sys.stdout.flush()
        reach("waiting")
    elif cmd == "question-gate" and not args:
        question_gate()
    elif cmd == "wake" and len(args) == 2:
        try:
            wake(*args, patience=300)
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
