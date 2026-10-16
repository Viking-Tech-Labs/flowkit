#!/usr/bin/env python3
"""Session lookups behind /coordinate:start, read from `claude agents --json --all`. The skill
calls this with plain arguments; nothing here asks.

  coordinate.py spawner               the flowkit-spawn of the handoff this session loaded (an older
                                      handoff's bin/ can come first on an inherited PATH)
  coordinate.py self                  this session: name, id, session id (the coordinator's name
                                      is what its workers message)
  coordinate.py workers <name> ...    one line per worker: live (message it), retired (a stopped
                                      background session: wake it), or missing
  coordinate.py wake <name> <message> wake a retired background worker with the message as its
                                      prompt, under its own id and saved options
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def fail(msg, code=2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def agents():
    try:
        out = subprocess.run(["claude", "agents", "--json", "--all"], capture_output=True,
                             text=True, timeout=30).stdout
        return json.loads(out or "[]")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        fail("cannot read `claude agents --json --all`", 3)


def parent(pid):
    try:
        out = subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True,
                             text=True).stdout.strip()
        return int(out) if out else 0
    except (OSError, ValueError):
        return 0


def state(entry):
    """live: running, SendMessage reaches it; retired: a background session with no process,
    its conversation kept; ended: any other entry without a process."""
    if entry is None:
        return "missing"
    if entry.get("pid"):
        return "live"
    return "retired" if entry.get("kind") == "background" else "ended"


def pick(rows, name):
    """The entry of that name: a live one first, else the newest."""
    named = [r for r in rows if r.get("name") == name]
    named.sort(key=lambda r: (bool(r.get("pid")), r.get("startedAt") or 0), reverse=True)
    return named[0] if named else None


def cmd_spawner():
    # A plugin dir loaded for this session first, then the installed handoff, then PATH.
    dirs = [Path(d) for d in os.environ.get("CLAUDE_CODE_PLUGIN_DIRS", "").split(":") if d]
    try:
        installed = json.loads((CLAUDE / "plugins" / "installed_plugins.json").read_text())
        dirs += [Path(e["installPath"]) for k, v in installed.get("plugins", {}).items()
                 if k.startswith("handoff@") for e in v if e.get("installPath")]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    for d in dirs:
        f = d / "bin" / "flowkit-spawn"
        if os.access(f, os.X_OK):
            print(f)
            return
    found = shutil.which("flowkit-spawn")
    if not found:
        fail("no flowkit-spawn: install handoff (claude plugin install handoff@flowkit)", 7)
    print(found)


def cmd_self():
    rows = agents()
    by_pid = {r["pid"]: r for r in rows if r.get("pid")}
    pid = os.getppid()
    for _ in range(8):  # the Bash tool's shell is a child of the claude process, maybe nested
        if pid in by_pid:
            r = by_pid[pid]
            print(f"name: {r.get('name') or '-'}")
            print(f"id: {r.get('id') or '-'}")
            print(f"session: {r.get('sessionId')}")
            return
        pid = parent(pid)
        if pid <= 1:
            break
    fail("this session is not listed by `claude agents`; name it with /rename and retry", 4)


def cmd_workers(names):
    rows = agents()
    for name in names:
        r = pick(rows, name)
        print(name, state(r), (r or {}).get("id") or "-", (r or {}).get("sessionId") or "-",
              (r or {}).get("cwd") or "-")


def cmd_wake(name, message):
    r = pick(agents(), name)
    st = state(r)
    if st == "live":
        fail(f"{name} is live: message it with SendMessage", 5)
    if st != "retired":
        fail(f"{name} is {st}: no background session to wake", 6)
    # No other flag: a resume that passes any starts a copy instead of waking it.
    p = subprocess.run(["claude", "--bg", "--resume", r["sessionId"], message],
                       capture_output=True, text=True, cwd=r.get("cwd") or None)
    print((p.stdout + p.stderr).strip())
    sys.exit(p.returncode)


def main(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__.strip())
        return
    cmd, rest = argv[0], argv[1:]
    if cmd == "spawner" and not rest:
        cmd_spawner()
    elif cmd == "self" and not rest:
        cmd_self()
    elif cmd == "workers" and rest:
        cmd_workers(rest)
    elif cmd == "wake" and len(rest) == 2:
        cmd_wake(*rest)
    else:
        fail(__doc__.strip())


if __name__ == "__main__":
    main(sys.argv[1:])
