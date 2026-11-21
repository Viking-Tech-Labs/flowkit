#!/usr/bin/env python3
"""Session lookups behind /coordinate:start, read from `claude agents --json --all`. The skill
calls this with plain arguments; nothing here asks.

  coordinate.py spawner               the flowkit-spawn of the handoff this session loaded (an older
                                      handoff's bin/ can come first on an inherited PATH)
  coordinate.py self                  this session: name, id, session id (the coordinator's name
                                      is what its workers message)
  coordinate.py self --growth         and the context it added in the last 24 h, from its
                                      transcript, against capacity.conf's coordinator_growth
  coordinate.py workers <name> ...    one line per worker: live (message it), retired (a stopped
                                      background session: wake it), or missing
  coordinate.py wake <name> <message> wake a retired background worker with `wake: <message>` as
                                      its prompt, under its own id and saved options
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
DAY = 24 * 3600
GROWTH = 300_000  # capacity.conf's coordinator_growth when it sets none


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


def growth(path, t):
    """Context tokens the session added in the 24 h before t: what each compaction in that window
    dropped (preTokens - postTokens, else the next turn's size), plus the size now, less the size
    at the window's start. A count of compactions would not do: one at 30% drops less than one at
    69%. Returns (tokens, compactions, size now)."""
    size = start = dropped = count = 0
    pending = None  # a compaction in the window whose postTokens the transcript does not give
    with open(path, errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
                at = datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00")).timestamp()
            except (ValueError, KeyError, TypeError, AttributeError):
                continue
            if at > t:  # a day in the past (capacity report): what came later is not its growth
                break
            if e.get("isSidechain"):
                continue
            if e.get("type") == "assistant":
                u = (e.get("message") or {}).get("usage") or {}
                n = sum(u.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens",
                                                "cache_read_input_tokens", "output_tokens"))
                if not n:
                    continue
                if pending is not None:
                    dropped += max(pending - n, 0)
                    pending = None
                size = n
            elif e.get("subtype") == "compact_boundary":
                meta = e.get("compactMetadata") or {}
                pre, post = meta.get("preTokens") or size, meta.get("postTokens")
                if at >= t - DAY:
                    count += 1
                    if post is None:
                        pending = pre
                    else:
                        dropped += max(pre - post, 0)
                size = post or 0
            else:
                continue
            if at < t - DAY:
                start = size
    return size - start + dropped, count, size


def growth_limit():
    """coordinator_growth from capacity.conf's machine keys (`300k`, `1.5m`), else GROWTH."""
    path = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "flowkit" / "capacity.conf"
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return GROWTH
    for raw in lines:
        line = raw.split("#", 1)[0].strip()
        if line.startswith("["):
            break
        m = re.match(r"^coordinator_growth\s*=\s*([\d.]+)([km]?)$", line, re.I)
        if m:
            return int(float(m.group(1)) * {"k": 1_000, "m": 1_000_000}.get(m.group(2).lower(), 1))
    return GROWTH


def tokens(n):
    return f"{n / 1_000_000:.1f}m" if n >= 1_000_000 else f"{round(n / 1000)}k"


def cmd_self(with_growth=False):
    rows = agents()
    by_pid = {r["pid"]: r for r in rows if r.get("pid")}
    pid = os.getppid()
    for _ in range(8):  # the Bash tool's shell is a child of the claude process, maybe nested
        if pid in by_pid:
            r = by_pid[pid]
            print(f"name: {r.get('name') or '-'}")
            print(f"id: {r.get('id') or '-'}")
            print(f"session: {r.get('sessionId')}")
            if with_growth:
                found = sorted(CLAUDE.glob(f"projects/*/{r.get('sessionId')}.jsonl"))
                if not found:
                    fail(f"no transcript for session {r.get('sessionId')} under {CLAUDE / 'projects'}", 8)
                added, count, size = growth(found[0], time.time())
                limit = growth_limit()
                print(f"growth: {tokens(added)} in 24 h ({count} compaction{'s' if count != 1 else ''}, "
                      f"now {tokens(size)}) · {'over' if added > limit else 'under'} {tokens(limit)}")
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
    # No other flag: a resume that passes any starts a copy instead of waking it. The prefix marks the
    # prompt as a session's, so handoff's away gate does not take it for one the user typed.
    p = subprocess.run(["claude", "--bg", "--resume", r["sessionId"], f"wake: {message}"],
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
    elif cmd == "self" and rest in ([], ["--growth"]):
        cmd_self(bool(rest))
    elif cmd == "workers" and rest:
        cmd_workers(rest)
    elif cmd == "wake" and len(rest) == 2:
        cmd_wake(*rest)
    else:
        fail(__doc__.strip())


if __name__ == "__main__":
    main(sys.argv[1:])
