#!/usr/bin/env python3
"""Files behind the handoff skills: the repo's pick-up doc, the ladder file, the away flag,
saved prompts and story files. The skills call this with plain arguments; nothing here asks.

  handoff.py state <data-dir> <repo>             what a close needs to know, one line each
  handoff.py pickup-doc <data-dir> <repo> <doc>  remember the repo's pick-up doc
  handoff.py save-prompt <name>                  stdin -> ~/.claude/spawned/<name>.md
  handoff.py record <slug>                       stdin appended to ~/.claude/stories/<slug>.md
  handoff.py away | back | away-status           the ~/.claude/away flag (12 hours)
  handoff.py statusline diff|install <root>      the status line that records context and cost
  handoff.py refresh <root>                      update an installed status line (SessionStart)
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
STATUSLINE = CONFIG / "statusline.py"
SETTINGS = CLAUDE / "settings.json"
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


def away_since():
    """The stamp in the away flag when it is younger than AWAY_HOURS, else None."""
    try:
        if time.time() - AWAY.stat().st_mtime > AWAY_HOURS * 3600:
            return None
        return AWAY.read_text().strip() or "set"
    except OSError:
        return None


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


def record(slug):
    STORIES.mkdir(parents=True, exist_ok=True)
    path = STORIES / f"{safe(slug, 'the story slug')}.md"
    text = sys.stdin.read().strip("\n")
    if not text:
        fail("nothing to record: the close goes on stdin")
    head = "" if path.exists() else f"# {slug}\n"
    with path.open("a") as f:
        f.write(f"{head}\n{text}\n")
    print(path)


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


def refresh(root):
    """Keep an installed status line at the plugin's version. Silent: its output reaches context."""
    source = Path(root) / "scripts" / "statusline.py"
    try:
        if STATUSLINE.is_file() and STATUSLINE.read_bytes() != source.read_bytes():
            shutil.copyfile(source, STATUSLINE)
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
    elif cmd == "back" and not args:
        AWAY.unlink(missing_ok=True)
        print("away: off")
    elif cmd == "statusline" and len(args) == 2 and args[0] in ("diff", "install"):
        statusline(*args)
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
