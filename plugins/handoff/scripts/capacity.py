#!/usr/bin/env python3
"""flowkit capacity: how many chains this machine runs at once, and who holds each named resource.

Slots are counted when asked, from `claude agents` and the story files, so nothing has to be given
back. Resources (a simulator, a dev-server port) are leased to a session id and go free with the
holder's recorded close, its death or half an hour idle. The numbers live in
`${XDG_CONFIG_HOME:-~/.config}/flowkit/capacity.conf`; with no file a machine runs 3 chains and
declares no resources. State lives under `${CLAUDE_CONFIG_DIR:-~/.claude}/capacity/`."""
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

DEFAULTS = {"slots": 3, "slice_minutes": 50, "coordinator_growth": 300_000}
STALL = 2 * 3600  # a story session idle this long with no close is shown as stalled and stops counting
SESSION_LINE = re.compile(r"^Session: ([0-9a-f-]{36})\b", re.M)  # a recorded close names its session
HEADING = re.compile(r"^## (.+?) · \d{4}-\d\d-\d\d", re.M)  # a close section, or the reporter's final report
PROMISED = re.compile(r"^Next: (?:spawned|started) (.*)$", re.M)  # the sessions a close launched
LAUNCHED = re.compile(r"^> launched (\S+)", re.M)  # flowkit-spawn's line for every story launch
COORDINATOR = re.compile(r"^Coordinator: (\S+)", re.M)  # a coordination board names its coordinator
COORD_NAME = re.compile(r"(?:^|-)coord(?:inator)?(?:-\d+)?$")  # coord-1, story-coord, story-coordinator
SECTION = re.compile(r"^\[(.+)\]$")
RESOURCE = re.compile(r"^resource\s+([A-Za-z0-9][A-Za-z0-9._-]*)\s*=\s*(.*)$")
SETTING = re.compile(r"^([a-z_]+)\s*=\s*(\S+)$")


def config_path():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "flowkit" / "capacity.conf"


def number(text):
    """`3`, `300k`, `1.5m`."""
    scale = {"k": 1_000, "m": 1_000_000}.get(text[-1:].lower(), 1)
    return int(float(text[:-1] if scale > 1 else text) * scale)


def load_config(path=None):
    """Machine keys, and a `[<repo path>]` section per repo with its own `slots` and its resources.
    A line that does not parse is skipped with one stderr line, so a typo never stops a launch."""
    cfg = {**DEFAULTS, "repos": {}}
    try:
        lines = (path or config_path()).read_text().splitlines()
    except OSError:
        return cfg
    section = None
    for n, raw in enumerate(lines, 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if m := SECTION.match(line):
            key = os.path.realpath(os.path.expanduser(m.group(1).strip()))
            section = cfg["repos"].setdefault(key, {"resources": {}})
        elif m := RESOURCE.match(line):
            if section is None:
                print(f"capacity.conf:{n}: a resource belongs under a [<repo path>] section", file=sys.stderr)
            else:
                section["resources"][m.group(1)] = m.group(2).strip()
        elif (m := SETTING.match(line)) and (m.group(1) in DEFAULTS if section is None else m.group(1) == "slots"):
            try:
                (cfg if section is None else section)[m.group(1)] = number(m.group(2))
            except ValueError:
                print(f"capacity.conf:{n}: not a number: {m.group(2)}", file=sys.stderr)
        else:
            print(f"capacity.conf:{n}: not understood: {line}", file=sys.stderr)
    return cfg


def main_checkout(tree):
    """The main checkout of tree's repository (a worktree's too), realpath; tree itself outside git."""
    out = subprocess.run(["git", "-C", str(tree), "rev-parse", "--git-common-dir"],
                         capture_output=True, text=True)
    if out.returncode:
        return os.path.realpath(tree)
    return os.path.dirname(os.path.realpath(os.path.join(tree, out.stdout.strip())))


def repo_section(cfg, tree):
    """The `[<repo path>]` section tree's main checkout falls under, or None."""
    return cfg["repos"].get(main_checkout(tree))


def declared(cfg):
    """Every resource name any repo declares: names are machine-wide."""
    return {name for section in cfg["repos"].values() for name in section["resources"]}


def claude_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def now():
    """The clock; FLOWKIT_CAPACITY_NOW (epoch seconds) stands in for it in tests."""
    return float(os.environ.get("FLOWKIT_CAPACITY_NOW") or time.time())


def snapshot():
    """`claude agents --json --all`, or None when the answer is unknown: the call failed, hung past
    FLOWKIT_CAPACITY_AGENTS_TIMEOUT (15 s) or came back in a shape this script does not know. Unknown
    is never read as "everyone is dead". Take it before the lock, never under it."""
    timeout = float(os.environ.get("FLOWKIT_CAPACITY_AGENTS_TIMEOUT") or 15)
    try:
        proc = subprocess.Popen(["claude", "agents", "--json", "--all"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, start_new_session=True)
    except OSError:
        return None
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return None
    try:
        rows = json.loads(out) if proc.returncode == 0 else None
    except ValueError:
        return None
    if not isinstance(rows, list) or not all(isinstance(r, dict) and isinstance(r.get("sessionId"), str)
                                             for r in rows):
        return None
    return rows


def liveness(rows, sid):
    """live, dead or unknown."""
    if rows is None:
        return "unknown"
    return "live" if any(r.get("sessionId") == sid and r.get("pid") for r in rows) else "dead"


def stories():
    """Every story session's name with its story's slug (a close heading, a `Next:` name or a
    `> launched` line), and the session ids with a recorded close."""
    members, closed = {}, set()
    for path in sorted((claude_dir() / "stories").glob("*.md")):
        if path.name.endswith(".tests.md"):
            continue
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        closed.update(SESSION_LINE.findall(text))
        names = set(HEADING.findall(text)) | set(LAUNCHED.findall(text))
        for rest in PROMISED.findall(text):
            names.update(n.strip() for n in re.split(r",\s*", re.sub(r"\s*\(.*$", "", rest)))
        for name in sorted(names - {"", "none", "Final report"}):
            members.setdefault(name, path.stem)
    return members, closed


def coordinators():
    """The names on the coordination boards (`Coordinator: <name> · …`)."""
    names = set()
    for path in (claude_dir() / "coordination").glob("*.md"):
        try:
            names.update(m.strip("`") for m in COORDINATOR.findall(path.read_text(errors="replace")))
        except OSError:
            pass
    return names


_TOPLEVEL = {}


def toplevel(cwd):
    """The working tree a session runs in, realpath; the directory itself outside git."""
    if cwd not in _TOPLEVEL:
        out = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        _TOPLEVEL[cwd] = os.path.realpath(out.stdout.strip() if out.returncode == 0 else cwd)
    return _TOPLEVEL[cwd]


def last_touch(row):
    """When the session last wrote its transcript or a fork's, else when it started; None if unknown."""
    sid = row.get("sessionId")
    times = []
    for transcript in (claude_dir() / "projects").glob(f"*/{sid}.jsonl"):
        forks = transcript.with_suffix("") / "subagents"
        for path in [transcript, *(forks.glob("*") if forks.is_dir() else [])]:
            try:
                times.append(path.stat().st_mtime)
            except OSError:
                pass
    if not times and isinstance(row.get("startedAt"), (int, float)):
        times.append(row["startedAt"] / 1000)
    return max(times) if times else None


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OverflowError):
        return True
    except (TypeError, ValueError):
        return False
    return True


def markers():
    """The in-flight flowkit-spawn launches: launching/<pid>.json, with whether that pid lives."""
    out = []
    for path in sorted((claude_dir() / "capacity" / "launching").glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            data = {}
        out.append({**(data if isinstance(data, dict) else {}), "pid": path.stem, "path": path,
                    "alive": pid_alive(path.stem)})
    return out


def chains(rows, t=None):
    """The working trees with a live story session in them, or None when liveness is unknown. A
    reporter or coordinator does not count, nor a session whose close is recorded unless it is busy
    again (a hand resume); a tree whose sessions all sat 2 h with no close is `stalled` and does not
    count. A live launching/ marker holds its tree as `launching`. Sessions sharing a tree (a review
    beside the next slice) share its slot."""
    if rows is None:
        return None
    t = now() if t is None else t
    members, closed = stories()
    coords = coordinators()
    trees = {}
    for row in rows:
        name, sid = row.get("name") or "", row.get("sessionId")
        if not row.get("pid") or not row.get("cwd") or name not in members:
            continue
        if name.endswith("-report") or COORD_NAME.search(name) or name in coords:
            continue
        busy = row.get("status") == "busy"
        if sid in closed and not busy:
            continue
        touched = last_touch(row)
        tree = toplevel(row["cwd"])
        chain = trees.setdefault(tree, {"tree": tree, "repo": main_checkout(tree), "story": members[name],
                                        "sessions": []})
        chain["sessions"].append({"name": name, "session": sid, "status": row.get("status") or "",
                                  "stalled": not busy and touched is not None and t - touched > STALL})
    for chain in trees.values():
        chain["state"] = "stalled" if all(s["stalled"] for s in chain["sessions"]) else "running"
    for mark in markers():
        tree = mark.get("tree")
        if mark["alive"] and tree and os.path.realpath(tree) not in trees:
            tree = os.path.realpath(tree)
            trees[tree] = {"tree": tree, "repo": main_checkout(tree), "story": mark.get("story") or "",
                           "sessions": [], "state": "launching", "pid": mark["pid"]}
    return sorted(trees.values(), key=lambda c: c["tree"])


def slots(cfg, found):
    """Slots used and allowed, for the machine and per configured repo; None when unknown."""
    if found is None:
        return None
    counted = [c for c in found if c["state"] != "stalled"]
    repos = {path: {"slots": section["slots"], "used": sum(c["repo"] == path for c in counted)}
             for path, section in cfg["repos"].items() if "slots" in section}
    return {"slots": cfg["slots"], "used": len(counted), "repos": repos}
