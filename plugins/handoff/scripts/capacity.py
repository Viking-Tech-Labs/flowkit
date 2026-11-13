#!/usr/bin/env python3
"""flowkit capacity: how many chains this machine runs at once, and who holds each named resource.

Slots are counted when asked, from `claude agents` and the story files, so nothing has to be given
back. Resources (a simulator, a dev-server port) are leased to a session id and go free with the
holder's recorded close, its death or half an hour idle. The numbers live in
`${XDG_CONFIG_HOME:-~/.config}/flowkit/capacity.conf`; with no file a machine runs 3 chains and
declares no resources. State lives under `${CLAUDE_CONFIG_DIR:-~/.claude}/capacity/`."""
import argparse
import contextlib
import fcntl
import json
import os
import re
import signal
import statistics
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULTS = {"slots": 3, "slice_minutes": 50, "coordinator_growth": 300_000}
STALL = 2 * 3600  # a story session idle this long with no close is shown as stalled and stops counting
IDLE = 30 * 60  # a holder not busy with its transcript untouched this long gives its resources back
ME = "me"  # the holder of the user's test hold
LAUNCH = "launch:"  # holder prefix of what an in-flight flowkit-spawn holds: launch:<its pid>
QUEUED = 11  # the gate's exit code when a launch is queued, which flowkit-spawn exits with
HOLD_STALE = 2 * 3600  # a load hold this old is ignored, as flowkit-spawn ignores it
STATUS_VERSION = 2  # status.json's schema; the board reads it, plugins/board/tests/fixtures/capacity-status.ts shows it
SPAN_MAX = 12 * 3600  # a slice span longer than this is a parked chain, not a slice: left out of the medians
MIN_SPANS = 3  # a repo with fewer slice spans in the log than this is planned at slice_minutes
OVERDUE = 10 * 60  # a running slice already past its median is given this long yet
ITEM = re.compile(r"^(?P<name>[^=\s]+)=(?P<tree>.+?)(?::(?P<slices>\d+)(?::(?P<needs>[A-Za-z0-9._+,-]*))?)?$")
TO_TEST = re.compile(r"\bto test\b", re.I)  # a test-queue state that waits on the user's testing
NEEDS = re.compile(r"(?:^|\s{2,})Needs:\s*(.*?)\s*(?=\s{2,}\S+:|$)", re.M)  # a prompt header's resources
LEFT = re.compile(r"(?:^|\s{2,})Left:\s*(\d+)", re.M)  # a prompt header's slices left after this one
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
    for repo, lim in limits(now()).items():
        section = cfg["repos"].setdefault(repo, {"resources": {}})
        section["slots"] = max(1, section.get("slots", cfg["slots"]) + lim["delta"])
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


def chains(rows, t=None, ending=()):
    """The working trees with a live story session in them, or None when liveness is unknown. A
    reporter or coordinator does not count, nor a session whose close is recorded unless it is busy
    again (a hand resume), nor one in ending (a close being recorded right now: still busy); a tree whose sessions all sat 2 h with no close is `stalled` and does not
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
        if (sid in closed and not busy) or sid in ending:
            continue
        touched = last_touch(row)
        tree = toplevel(row["cwd"])
        chain = trees.setdefault(tree, {"tree": tree, "repo": main_checkout(tree), "story": members[name],
                                        "sessions": [], "pid": None})
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


def state_dir():
    return claude_dir() / "capacity"


@contextlib.contextmanager
def lock():
    """capacity/.lock, tried without blocking for FLOWKIT_CAPACITY_LOCK_WAIT (10 s), then gone on
    without, with one stderr line: a stuck holder must never stall every launch."""
    state_dir().mkdir(parents=True, exist_ok=True)
    fd = os.open(state_dir() / ".lock", os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + float(os.environ.get("FLOWKIT_CAPACITY_LOCK_WAIT") or 10)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    print("capacity: the lock stayed busy, going on without it", file=sys.stderr)
                    break
                time.sleep(0.1)
        yield
    finally:
        os.close(fd)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def log(event, t=None, **fields):
    """One line in capacity/log.jsonl: take, release, free, reclaim, hold."""
    state_dir().mkdir(parents=True, exist_ok=True)
    with open(state_dir() / "log.jsonl", "a") as f:
        f.write(json.dumps({"t": round(now() if t is None else t, 3), "event": event, **fields}) + "\n")


def leases():
    """Resource name -> its lease: session, name, story, tree, taken, why (and until for a test hold)."""
    out = {}
    for path in sorted((state_dir() / "resources").glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("session"):
            out[path.stem] = data
    return out


def why_free(lease, rows, closed, t):
    """Why a lease is over, or None: a test hold past its minutes; else, only while liveness is known,
    the holder's close recorded, no pid (reclaimed) or not busy with its transcript untouched 30 min."""
    if lease["session"] == ME:
        return "expired" if t >= lease.get("until", 0) else None
    if lease["session"].startswith(LAUNCH):
        return None if pid_alive(lease["session"][len(LAUNCH):]) else "dead"
    if rows is None:
        return None
    if lease["session"] in closed:
        return "closed"
    row = next((r for r in rows if r.get("sessionId") == lease["session"] and r.get("pid")), None)
    if row is None:
        return "dead"
    touched = last_touch(row)
    if row.get("status") != "busy" and touched is not None and t - touched > IDLE:
        return "idle"
    return None


def refresh(rows, t):
    """Under the lock: drop the leases that are over and the markers whose flowkit-spawn is gone. A
    dead holder's lease and a dead marker are logged as `reclaim`, the rest as `free`."""
    _, closed = stories()
    for name, lease in leases().items():
        reason = why_free(lease, rows, closed, t)
        if reason:
            (state_dir() / "resources" / f"{name}.json").unlink(missing_ok=True)
            log("reclaim" if reason == "dead" else "free", t, resource=name, reason=reason,
                session=lease["session"], holder=lease.get("name", ""), since=lease.get("taken"))
    for mark in markers():
        if not mark["alive"]:
            mark["path"].unlink(missing_ok=True)
            log("reclaim", t, launching=mark["pid"], tree=mark.get("tree", ""), story=mark.get("story", ""))


def holder(rows, sid):
    """The name, story and tree a session id has, as far as the snapshot tells."""
    row = next((r for r in rows or [] if r.get("sessionId") == sid), {})
    name = row.get("name") or ""
    return {"name": name, "story": stories()[0].get(name, ""),
            "tree": toplevel(row["cwd"]) if row.get("cwd") else ""}


def busy_line(held):
    return "; ".join(f"{n} is held by {l.get('name') or l['session']} since "
                     f"{time.strftime('%H:%M', time.localtime(l.get('taken', 0)))}" for n, l in sorted(held.items()))


def take(rows, sid, names, why="take", t=None):
    """All of names for sid, or none of them: (exit code, message). Refused while sid holds anything
    outside names, so a session never waits for more while holding some (no hold-and-wait)."""
    t = now() if t is None else t
    with lock():
        refresh(rows, t)
        held = leases()
        mine = {n for n, lease in held.items() if lease["session"] == sid}
        if mine - names:
            return 3, (f"holding {', '.join(sorted(mine - names))} outside the set: release it first, "
                       f"then take {','.join(sorted(mine | names))} holding nothing")
        others = {n: lease for n, lease in held.items() if n in names and lease["session"] != sid}
        if others:
            return 1, f"busy: {busy_line(others)}"
        who = holder(rows, sid)
        for name in sorted(names - mine):
            write_json(state_dir() / "resources" / f"{name}.json",
                       {"session": sid, **who, "taken": t, "why": why})
            log("take", t, resource=name, session=sid, holder=who["name"], why=why)
        write_status(rows, t)
    return 0, f"took {', '.join(sorted(names))}"


def release(rows, sid, names=None, t=None):
    """Give back what sid holds (all of it without names): (exit code, message)."""
    t = now() if t is None else t
    with lock():
        refresh(rows, t)
        held = leases()
        gone = sorted(n for n, lease in held.items() if lease["session"] == sid and (not names or n in names))
        for name in gone:
            lease = held[name]
            (state_dir() / "resources" / f"{name}.json").unlink(missing_ok=True)
            log("release", t, resource=name, session=sid, holder=lease.get("name", ""), since=lease.get("taken"))
        write_status(rows, t)
    return 0, f"released {', '.join(gone)}" if gone else "held nothing"


def hold(rows, names, minutes, t=None):
    """The user's test hold: names for `me` for minutes from now, all or none; a hold already the
    user's is renewed."""
    t = now() if t is None else t
    with lock():
        refresh(rows, t)
        held = leases()
        others = {n: lease for n, lease in held.items() if n in names and lease["session"] != ME}
        if others:
            return 1, f"busy: {busy_line(others)}"
        for name in sorted(names):
            write_json(state_dir() / "resources" / f"{name}.json",
                       {"session": ME, "name": ME, "story": "", "tree": "",
                        "taken": held.get(name, {}).get("taken", t), "why": "test hold", "until": t + minutes * 60})
            log("hold", t, resource=name, minutes=minutes)
        write_status(rows, t)
    return 0, f"held {', '.join(sorted(names))} for you until {time.strftime('%H:%M', time.localtime(t + minutes * 60))}"


def status_data(rows, t):
    """The snapshot status.json holds: slots, chains, resources and the queue, oldest first."""
    cfg = load_config()
    found = chains(rows, t)
    used = slots(cfg, found)
    held = leases()
    resources = []
    for name in sorted(declared(cfg) | set(held)):
        lease = held.get(name)
        resources.append({"name": name, "session": lease and lease["session"], "holder": lease and lease.get("name"),
                          "story": lease and lease.get("story"), "since": lease and lease.get("taken"),
                          "why": lease and lease.get("why"), "until": lease and lease.get("until")})
    waiting = entries()
    plan = forecast(cfg, found, waiting, [], t) if waiting else {}
    return {"version": STATUS_VERSION, "at": t, "agents": "unknown" if rows is None else "known",
            "slots": cfg["slots"], "used": used and used["used"],
            "repos": [{"path": path, **counts} for path, counts in sorted(used["repos"].items())] if used else [],
            "chains": found or [], "resources": resources,
            "queue": [{"id": e["id"], "name": e.get("name", ""), "story": e.get("story", ""), "tree": e.get("tree", ""),
                       "needs": sorted(e.get("needs", [])), "left": e.get("left"), "queued": e.get("queued"),
                       "by": e.get("by", ""), "continuation": bool(e.get("continuation")), "why": e.get("why", ""),
                       "eta": plan[("queue", e["id"])]["start"] or e.get("eta")} for e in waiting]}


def write_status(rows, t):
    write_json(state_dir() / "status.json", status_data(rows, t))


def clock(t):
    return time.strftime("%H:%M", time.localtime(t))


def status_lines(data):
    """One line for the slots, then one per repo limit, chain and resource."""
    if data["agents"] == "unknown":
        lines = [f"slots ?/{data['slots']} · claude agents did not answer: nothing is reclaimed, no new chain starts"]
    else:
        lines = [f"slots {data['used']}/{data['slots']}"]
    lines += [f"repo {r['path']} · {r['used']}/{r['slots']}" for r in data["repos"]]
    for c in data["chains"]:
        who = ", ".join(f"{s['name']} ({'stalled' if s['stalled'] else s['status']})" for s in c["sessions"])
        lines.append(f"chain {c['story'] or '-'} · {c['state']} · {c['tree']}" +
                     (f" · {who}" if who else f" · flowkit-spawn pid {c['pid']}"))
    for r in data["resources"]:
        if not r["session"]:
            lines.append(f"resource {r['name']} · free")
        elif r["session"] == ME:
            lines.append(f"resource {r['name']} · held for you until {clock(r['until'])} (test hold)")
        else:
            lines.append(f"resource {r['name']} · held by {r['holder'] or r['session']}"
                         f"{' (' + r['story'] + ')' if r['story'] else ''} since {clock(r['since'])} ({r['why']})")
    for n, q in enumerate(data["queue"], 1):
        lines.append(f"queue #{n} {q['name']}{' (' + q['story'] + ')' if q['story'] else ''} · {q['why']}"
                     f"{' · needs ' + ', '.join(q['needs']) if q['needs'] else ''} · queued {clock(q['queued'])} by {q['by']}"
                     f" · ~{clock(q['eta'])}")
    return lines


def status(rows, t=None):
    """Drop what is over, write status.json and return it."""
    t = now() if t is None else t
    with lock():
        refresh(rows, t)
        write_status(rows, t)
    return status_data(rows, t)


def prompt_header(path):
    """The resources a prompt's `Needs:` line names (`none` is none) and the slices its `Left:`
    line says are left after this one (None without one), read from the header only: the lines
    before the prompt's first blank one."""
    try:
        text = Path(path).read_text(errors="replace")
    except OSError:
        return set(), None
    head = []
    for line in text.splitlines():
        if not line.strip():
            break
        head.append(line)
    head = "\n".join(head)
    needs = NEEDS.search(head)
    left = LEFT.search(head)
    return (names_arg([needs.group(1)]) - {"none", "-"} if needs else set()), (int(left.group(1)) if left else None)


def entries():
    """The queued launches, oldest first: queue/<id>.json with its id."""
    out = []
    for path in (state_dir() / "queue").glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if path.stem.isdigit() and isinstance(data, dict):
            out.append({**data, "id": int(path.stem)})
    return sorted(out, key=lambda e: e["id"])


def queued():
    """Whether anything is queued: one directory read, so an empty queue costs nothing."""
    try:
        return any(e.name.endswith(".json") for e in os.scandir(state_dir() / "queue"))
    except OSError:
        return False


def load_held():
    """Whether ~/.claude/load-hold is set and younger than 2 hours (its first line's ISO time, else
    the file's own), as flowkit-spawn reads it; FLOWKIT_IGNORE_HOLD skips it there and here."""
    path = claude_dir() / "load-hold"
    if os.environ.get("FLOWKIT_IGNORE_HOLD"):
        return False
    try:
        at = path.stat().st_mtime
        first = path.read_text(errors="replace").splitlines()[:1]
    except OSError:
        return False
    try:
        at = datetime.fromisoformat(first[0].strip().replace("Z", "+00:00")).timestamp()
    except (IndexError, ValueError):
        pass
    return time.time() - at < HOLD_STALE


def full(cfg, found, tree):
    """Why a new chain in tree has no slot, or None: the machine's slots, or its repo's, are all
    used. A tree that already holds a counted chain (a review beside a slice) needs no slot."""
    counted = [c for c in found if c["state"] != "stalled"]
    if any(c["tree"] == tree for c in counted):
        return None
    if len(counted) >= cfg["slots"]:
        return f"slots {len(counted)}/{cfg['slots']}"
    repo = main_checkout(tree)
    section = cfg["repos"].get(repo) or {}
    used = sum(c["repo"] == repo for c in counted)
    if "slots" in section and used >= section["slots"]:
        return f"repo slots {used}/{section['slots']}"
    return None


def events():
    """capacity/log.jsonl, oldest first; a line that does not parse is skipped."""
    try:
        lines = (state_dir() / "log.jsonl").read_text(errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and isinstance(ev.get("t"), (int, float)):
            out.append(ev)
    return out


_REPO = {}


def repo_of(tree):
    """main_checkout, remembered: the log names trees long gone."""
    if tree not in _REPO:
        _REPO[tree] = main_checkout(tree) if os.path.isdir(tree) else os.path.realpath(tree)
    return _REPO[tree]


def starts(log_events):
    """Every slice start in the log, oldest first: a launch through the gate or a drained one, with
    its name, story, tree, repo, `Left:` and the launcher that called the gate."""
    queues = {ev.get("id"): ev for ev in log_events if ev["event"] == "queue"}
    out = []
    for ev in log_events:
        if ev["event"] not in ("launch", "dequeue"):
            continue
        q = queues.get(ev.get("id"), {}) if ev["event"] == "dequeue" else {}
        tree = ev.get("tree") or q.get("tree") or ""
        out.append({"t": ev["t"], "name": ev.get("name") or "", "story": ev.get("story") or "", "tree": tree,
                    "repo": ev.get("repo") or q.get("repo") or (repo_of(tree) if tree else ""),
                    "left": ev["left"] if "left" in ev else q.get("left")})
    return out


def medians(log_events):
    """Seconds per slice, per repo: the median span from a slice's start to its own gate call for
    the next slice (a launch or a queued successor, matched by launcher name, else by story), the
    minutes that call sat in a load hold taken off. A repo with fewer than MIN_SPANS spans is left
    out, so slice_minutes stands in for it; a span over SPAN_MAX is a parked chain, not a slice."""
    ends = [ev for ev in log_events if ev["event"] in ("launch", "queue") and ev.get("by") == "chain"]
    spans = {}
    for s in starts(log_events):
        end = next((e for e in ends if e["t"] > s["t"] and (
            e["launcher"] == s["name"] if e.get("launcher") else s["story"] and e.get("story") == s["story"])), None)
        if end is None:
            continue
        span = end["t"] - (end.get("held") or 0) - s["t"]
        if 0 < span <= SPAN_MAX:
            spans.setdefault(s["repo"], []).append(span)
    return {repo: statistics.median(v) for repo, v in spans.items() if len(v) >= MIN_SPANS}


def forecast(cfg, found, waiting, items, t, log_events=None):
    """When each queued launch, then each planned item, would start and be ready to test: drain's
    rules replayed (oldest first; a new chain needs a machine and a repo slot, a tree already
    running needs none, nor does a continuation; every need free at once; nobody passes an earlier
    one that waits for a slot or wants the same resource) over the running chains' remaining
    slices (`Left:` of their latest start) at per-repo medians. Items are {name, tree, slices,
    needs}. Returns {("queue", id) or ("item", n): {start, ready, wave}}; wave 1 starts at t, and
    a start of None could not be placed."""
    log_events = events() if log_events is None else log_events
    med = medians(log_events)

    def per(repo):
        return med.get(repo, cfg["slice_minutes"] * 60)
    latest = {os.path.realpath(s["tree"]): s for s in starts(log_events) if s["tree"]}
    held = leases()
    running, claimed = [], set()
    for c in found or []:
        if c["state"] == "stalled":
            continue
        s, m = latest.get(c["tree"]), per(c["repo"])
        end = max((s["t"] if s else t) + m, t + min(OVERDUE, m)) + ((s or {}).get("left") or 0) * m
        sids = {x["session"] for x in c.get("sessions", [])}
        needs = {n for n, lease in held.items() if lease["session"] != ME and (
            lease["session"] in sids or os.path.realpath(lease.get("tree") or "/") == c["tree"])}
        claimed |= needs
        running.append({"end": end, "tree": c["tree"], "repo": c["repo"], "needs": needs})
    others = [(n, lease.get("until") or t + cfg["slice_minutes"] * 60) for n, lease in held.items() if n not in claimed]
    pending = [{"key": ("queue", e["id"]), "tree": e.get("tree") or "", "needs": set(e.get("needs", [])),
                "slices": (e.get("left") or 0) + 1, "free": bool(e.get("continuation") or e.get("over_cap"))}
               for e in waiting]
    pending += [{"key": ("item", n), "tree": i["tree"], "needs": set(i["needs"]), "slices": max(i["slices"], 1),
                 "free": False} for n, i in enumerate(items)]
    for p in pending:
        p["repo"] = repo_of(p["tree"])

    def room(p):
        if p["free"] or any(r["tree"] == p["tree"] for r in running):
            return True
        limit = (cfg["repos"].get(p["repo"]) or {}).get("slots")
        return len(running) < cfg["slots"] and (limit is None or sum(r["repo"] == p["repo"] for r in running) < limit)
    out, at, waves = {}, t, [t]
    while pending:
        running = [r for r in running if r["end"] > at]
        busy = {n for r in running for n in r["needs"]} | {n for n, until in others if until > at}
        slot_wait, wanted = False, set()
        for p in list(pending):
            fits = p["free"] or not slot_wait and room(p)
            if fits and not p["needs"] & (busy | wanted):
                ready = at + p["slices"] * per(p["repo"])
                running.append({"end": ready, "tree": p["tree"], "repo": p["repo"], "needs": p["needs"]})
                busy |= p["needs"]
                if at not in waves:
                    waves.append(at)
                out[p["key"]] = {"start": at, "ready": ready, "wave": waves.index(at) + 1}
                pending.remove(p)
            else:
                slot_wait = slot_wait or not fits
                wanted |= p["needs"]
        later = [r["end"] for r in running if r["end"] > at] + [u for _, u in others if u > at]
        if not pending or not later:
            break
        at = min(later)
    out.update({p["key"]: {"start": None, "ready": None, "wave": None} for p in pending})
    return out


def eta(cfg, found, waiting, entry, t):
    """When a launch about to be queued behind waiting would start: the forecast's, else one slice on."""
    start = forecast(cfg, found, [*waiting, {**entry, "id": -1}], [], t)[("queue", -1)]["start"]
    return start if start is not None else t + cfg["slice_minutes"] * 60


def continues(me, story, tree):
    """Whether a launch by session me continues its own chain: me is a session of that story, or
    runs in the tree the launch goes to (a first slice started by hand that no story file names
    yet). A coordinator or a reporter never continues: what it launches is a chain of its own."""
    name = me.get("name") or ""
    if not name or name.endswith("-report") or COORD_NAME.search(name) or name in coordinators():
        return False
    return stories()[0].get(name) == story or bool(me.get("cwd")) and toplevel(me["cwd"]) == tree


def gate(rows, pid, tree, story, name, prompt, argv, me, over_cap=False, dry=False, t=None, held=0):
    """flowkit-spawn's gate, before it types anything: (exit code, message). A launch that opens a
    new chain on a full machine (or while liveness is unknown) is queued, as is one whose needs
    are not all free, or one that would pass an earlier waiter; a chain's own next slice never
    waits for a slot, and --over-cap skips the slot count. Resources are taken all or none, for
    `launch:<pid>`, before anything waits: a continuation's closer hands over what it holds only
    when every need is free at once; otherwise it gives back all it holds and the launch is queued
    holding nothing, so two closers never wait on each other. A launch that passes leaves the
    launching/<pid>.json marker for its slot until `launched` re-points or gives back. held: the
    seconds flowkit-spawn sat in a load hold first, logged so slice medians leave them out."""
    t = now() if t is None else t
    cfg = load_config()
    tree = toplevel(tree)
    needs, left = prompt_header(prompt) if prompt else (set(), None)
    stray = needs - declared(cfg)
    if stray:
        print(f"capacity: Needs names {', '.join(sorted(stray))}, not declared in {config_path()}: ignored",
              file=sys.stderr)
        needs -= stray
    sid = me.get("sessionId")
    cont = continues(me, story, tree)
    by = "chain" if cont else "coordinator" if me.get("name") and (
        COORD_NAME.search(me["name"]) or me["name"] in coordinators()) else "user"
    with lock():
        if not dry:
            refresh(rows, t)
        found = chains(rows, t)
        waiting = entries()
        why = None
        if not cont:
            room = "claude agents did not answer" if found is None else full(cfg, found, tree)
            if room is None and any(not e.get("continuation") for e in waiting):
                room = "behind the queue"
            if room and over_cap:
                if not dry:
                    log("over-cap", t, name=name, story=story, tree=tree, why=room)
            else:
                why = room
        leased = leases()
        mine = {n for n, lease in leased.items() if sid and lease["session"] == sid}
        wanted = {n for e in waiting for n in e.get("needs", [])}
        busy = {n: leased[n] for n in needs if n in leased and not (cont and n in mine)}
        later = sorted(n for n in needs - set(leased) if n in wanted)
        if not why and (busy or later):
            why = "; ".join(filter(None, [busy and f"busy: {busy_line(busy)}",
                                          later and f"{', '.join(later)} wanted by an earlier launch"]))
        if why:
            position = len(waiting) + 1
            start = eta(cfg, found, waiting, {"tree": tree, "needs": needs, "left": left, "continuation": cont,
                                              "over_cap": over_cap}, t)
            if dry:
                return 0, f"QUEUED #{position}, ~{clock(start)} · {why} (dry run: nothing queued)"
            if cont and mine:
                for n in sorted(mine):
                    (state_dir() / "resources" / f"{n}.json").unlink(missing_ok=True)
                    log("release", t, resource=n, session=sid, holder=leased[n].get("name", ""),
                        since=leased[n].get("taken"), why="queued successor")
            seq_path = state_dir() / "queue" / ".seq"
            try:
                seq = int(seq_path.read_text()) + 1
            except (OSError, ValueError):
                seq = max([e["id"] for e in waiting], default=0) + 1
            write_json(seq_path, seq)
            write_json(state_dir() / "queue" / f"{seq}.json",
                       {"name": name, "story": story, "tree": tree, "prompt": prompt, "argv": argv,
                        "needs": sorted(needs), "left": left, "continuation": cont, "queued": t, "by": by,
                        "over_cap": over_cap, "launcher": me.get("name") or "", "why": why, "eta": start})
            log("queue", t, id=seq, name=name, story=story, tree=tree, needs=sorted(needs), why=why, by=by,
                repo=repo_of(tree), left=left, launcher=me.get("name") or "", held=held, eta=start)
            write_status(rows, t)
            return QUEUED, f"QUEUED #{position}, ~{clock(start)} · id={seq} · {why}"
        kind = "continuation" if cont else "new chain"
        if dry:
            return 0, f"{kind}{', takes ' + ', '.join(sorted(needs)) if needs else ''}"
        reserve(pid, tree, story, name, needs, t, sid if cont else None)
        log("launch", t, name=name, story=story, tree=tree, needs=sorted(needs), kind=kind, by=by,
            repo=repo_of(tree), left=left, launcher=me.get("name") or "", held=held)
        write_status(rows, t)
    return 0, kind


def reserve(pid, tree, story, name, needs, t, closer=None):
    """Under the lock: the launching/<pid>.json marker, and needs leased to launch:<pid> (the
    closer's own leases among them handed over)."""
    write_json(state_dir() / "launching" / f"{pid}.json", {"tree": tree, "story": story, "name": name})
    for n in sorted(needs):
        write_json(state_dir() / "resources" / f"{n}.json",
                   {"session": f"{LAUNCH}{pid}", "name": name, "story": story, "tree": tree, "taken": t, "why": "launch"})
        log("take", t, resource=n, session=f"{LAUNCH}{pid}", holder=name, why="launch",
            **({"from": closer} if closer else {}))


def launched(rows, pid, name=None, t=None):
    """After flowkit-spawn: with the new session's name (it printed SPAWNED), what launch:<pid>
    holds goes to that session's id; without, or when `claude agents` does not list it, it goes
    back. The launching/ marker goes either way: the session, once listed, holds its own slot."""
    t = now() if t is None else t
    holder_id = f"{LAUNCH}{pid}"
    row = next((r for r in sorted(rows or [], key=lambda r: r.get("startedAt") or 0, reverse=True)
                if name and r.get("name") == name and r.get("pid")), None)
    with lock():
        held = {n: lease for n, lease in leases().items() if lease["session"] == holder_id}
        for n, lease in sorted(held.items()):
            if row:
                write_json(state_dir() / "resources" / f"{n}.json", {**lease, "session": row["sessionId"]})
                log("repoint", t, resource=n, session=row["sessionId"], holder=name)
            else:
                (state_dir() / "resources" / f"{n}.json").unlink(missing_ok=True)
                log("release", t, resource=n, session=holder_id, holder=lease.get("name", ""),
                    since=lease.get("taken"), why="launch failed" if not name else "session not listed")
        (state_dir() / "launching" / f"{pid}.json").unlink(missing_ok=True)
        write_status(rows, t)
    return 0, (f"{'handed ' + ', '.join(sorted(held)) + ' to ' + name if row else 'released ' + ', '.join(sorted(held))}"
               if held else "held nothing")


def spawn(entry):
    """Launches a queued entry as a detached flowkit-spawn with its argv; its output goes to
    capacity/launched/<id>.log. Returns the pid."""
    out = state_dir() / "launched" / f"{entry['id']}.log"
    out.parent.mkdir(parents=True, exist_ok=True)
    tool = Path(__file__).resolve().parent.parent / "bin" / "flowkit-spawn"
    with open(out, "a") as f:
        proc = subprocess.Popen([str(tool), "--from-queue", str(entry["id"]), *entry["argv"]],
                                stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT,
                                cwd=entry["tree"] if os.path.isdir(entry.get("tree") or "") else None,
                                start_new_session=True)
    return proc.pid


def drain(rows, t=None, ending=()):
    """Under the lock, oldest first: launches every queued entry whose slot (a continuation needs
    none) and needs are free, unless a load hold is set. An entry never passes an earlier one that
    waits for a slot, or for a resource it needs too. Its marker and leases are written for the
    new flowkit-spawn's pid before the lock goes, so the next gate counts them. ending: session
    ids whose close is being recorded right now (still busy). Returns the launched entries."""
    t = now() if t is None else t
    if not queued() or load_held():
        return []
    cfg = load_config()
    started = []
    with lock():
        refresh(rows, t)
        found = chains(rows, t, ending)
        held = set(leases())
        slot_wait, wanted = False, set()
        for e in entries():
            needs = set(e.get("needs", []))
            room = e.get("continuation") or e.get("over_cap") or (found is not None and not slot_wait and full(cfg, found, e["tree"]) is None)
            if room and not needs & (held | wanted):
                pid = spawn(e)
                reserve(pid, e["tree"], e.get("story", ""), e.get("name", ""), needs, t)
                (state_dir() / "queue" / f"{e['id']}.json").unlink(missing_ok=True)
                log("dequeue", t, id=e["id"], name=e.get("name", ""), story=e.get("story", ""), pid=pid,
                    waited=round(t - (e.get("queued") or t)), tree=e["tree"], repo=repo_of(e["tree"]),
                    left=e.get("left"))
                tie = ties().get(e.get("name"))
                if tie:
                    m = medians(events()).get(repo_of(e["tree"]), cfg["slice_minutes"] * 60)
                    testq_set(tie["story"], tie["item"], t, state="running", eta=t + ((e.get("left") or 0) + 1) * m)
                    tie_launch(e["name"], None)
                held |= needs
                if found is not None:
                    found.append({"tree": e["tree"], "repo": main_checkout(e["tree"]), "state": "launching"})
                started.append({**e, "pid": pid})
            else:
                slot_wait = slot_wait or not room
                wanted |= needs
        write_status(rows, t)
    return started


def tests_path(story):
    return claude_dir() / "stories" / f"{story}.tests.md"


def read_tests(story):
    """A story's test queue: its leading `#` lines, and one [item, MR, device, state, ETA] per line."""
    head, lines = [], []
    try:
        text = tests_path(story).read_text(errors="replace")
    except OSError:
        return head, lines
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            if not lines:
                head.append(line)
        elif line:
            fields = re.split(r"\s*·\s*", re.sub(r"^[-*]\s+", "", line))
            lines.append((fields + [""] * 4)[:4] + [" · ".join(fields[4:])])
    return head, lines


def testq_set(story, item, t, **fields):
    """Under the lock: the item's line in <story>.tests.md set (added at the end when new) with the
    fields given (mr, device, state, eta as epoch seconds or "" to clear), logged as `test` with the
    state it had, so planned and actual ready times can be read back from the log."""
    head, lines = read_tests(story)
    line = next((x for x in lines if x[0] == item), None)
    before = line[3] if line else None
    if line is None:
        line = [item, "", "", "", ""]
        lines.append(line)
    for n, key in enumerate(("mr", "device", "state", "eta"), 1):
        value = fields.get(key)
        if value is not None:
            line[n] = clock(value) if isinstance(value, (int, float)) else value
    write_tests(story, head, lines)
    log("test", t, story=story, item=item, op="set", before=before,
        **{k: v for k, v in fields.items() if v is not None})
    return " · ".join(line)


def testq_drop(story, item, t):
    head, lines = read_tests(story)
    kept = [x for x in lines if x[0] != item]
    if len(kept) == len(lines):
        return False
    write_tests(story, head, kept)
    log("test", t, story=story, item=item, op="drop", before=next(x[3] for x in lines if x[0] == item))
    return True


def write_tests(story, head, lines):
    """The whole file rewritten, or deleted once no line is left."""
    if not lines:
        tests_path(story).unlink(missing_ok=True)
        return
    rows = []
    for line in lines:
        fields = list(line)
        while fields and not fields[-1]:
            fields.pop()
        rows.append(" · ".join(fields))
    path = tests_path(story)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}")
    tmp.write_text("\n".join(head + rows) + "\n")
    os.replace(tmp, path)


def ties():
    """Launch name -> {story, item}: the test-queue line a queued launch moves on drain."""
    try:
        data = json.loads((state_dir() / "testq.json").read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def tie_launch(name, tie):
    data = ties()
    if tie:
        data[name] = tie
    else:
        data.pop(name, None)
    write_json(state_dir() / "testq.json", data)


def clock_arg(text, t):
    """`HH:MM` as the epoch of its next showing (one up to 6 h ago is today's); "" stays ""."""
    if not text:
        return ""
    hour, minute = (int(x) for x in text.split(":"))
    at = datetime.fromtimestamp(t).replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()
    return at + 86400 if at < t - 6 * 3600 else at


def limits(t):
    """Repo -> {delta, until, set}: the slot limits `limit` set that have not run out."""
    try:
        data = json.loads((state_dir() / "limits.json").read_text())
    except (OSError, ValueError):
        return {}
    return {repo: lim for repo, lim in data.items() if isinstance(lim, dict) and (lim.get("until") or 0) > t}


def limit(repo, delta, hours, t):
    """Under the lock: repo runs delta slots more (or fewer) than configured, never under one, for
    hours; a delta of 0 ends it. Logged as `limit`."""
    repo = main_checkout(os.path.expanduser(repo))
    with lock():
        data = limits(t)
        if delta:
            data[repo] = {"delta": delta, "until": t + hours * 3600, "set": t}
        else:
            data.pop(repo, None)
        write_json(state_dir() / "limits.json", data)
        log("limit", t, repo=repo, delta=delta, until=data.get(repo, {}).get("until"))
    if not delta:
        return f"repo {repo}: limit ended"
    return f"repo {repo}: {load_config()['repos'][repo]['slots']} slots until {clock(t + hours * 3600)}"


def waves(rows, specs, story="", write=False, t=None):
    """A batch's plan: one line per item (wave, start, ready, slices, needs; an item that needs no
    resource is shed to a chain of its own) and the coordinator's plan line. specs are
    `NAME=TREE[:SLICES[:NEEDS]]`, NEEDS joined by `+` or `,`, in launch order. With story, the
    plan line counts that story's test-queue lines waiting on the user's testing; with write,
    every item that does not start now gets its line there (`queued, wave N`), moved on drain."""
    t = now() if t is None else t
    cfg = load_config()
    items = []
    for spec in specs:
        m = ITEM.match(spec)
        if not m:
            raise ValueError(f"not NAME=TREE[:SLICES[:NEEDS]]: {spec}")
        needs = names_arg([m["needs"].replace("+", ",")]) if m["needs"] else set()
        stray = needs - declared(cfg)
        if stray:
            print(f"capacity: {m['name']} needs {', '.join(sorted(stray))}, not declared in {config_path()}: ignored",
                  file=sys.stderr)
        items.append({"name": m["name"], "tree": toplevel(os.path.expanduser(m["tree"])),
                      "slices": int(m["slices"] or 1), "needs": needs - stray})
    found = chains(rows, t)
    waiting = entries()
    plan = forecast(cfg, found, waiting, items, t)
    lines, now_, later, shed, lost = [], [], [], [], []
    for n, item in enumerate(items):
        p = plan[("item", n)]
        size = f"{item['slices']} slice{'s' if item['slices'] != 1 else ''}"
        needs = f" · needs {', '.join(sorted(item['needs']))}" if item["needs"] else " · shed"
        if p["start"] is None:
            lines.append(f"{item['name']} · not placed: this machine cannot run it · {size}{needs}")
            lost.append(item["name"])
            continue
        when = "now" if p["start"] == t else f"start ~{clock(p['start'])}"
        lines.append(f"{item['name']} · wave {p['wave']} · {when} · ready ~{clock(p['ready'])} · {size}{needs}")
        tag = item["name"] if p["start"] == t else f"{item['name']} ~{clock(p['start'])}"
        (shed if not item["needs"] else now_ if p["start"] == t else later).append(tag)
        if write and story and p["start"] != t:
            with lock():
                testq_set(story, item["name"], t, eta=p["ready"], state=f"queued, wave {p['wave']}" + (
                    f", needs {', '.join(sorted(item['needs']))}" if item["needs"] else ""))
                tie_launch(item["name"], {"story": story, "item": item["name"]})
    counted = [c for c in found or [] if c["state"] != "stalled"]
    trees = {c["tree"] for c in counted} | {e["tree"] for e in waiting if plan[("queue", e["id"])]["start"] == t} | {
        i["tree"] for n, i in enumerate(items) if plan[("item", n)]["start"] == t}
    parts = [f"Plan: {len(now_)} of {len(items)} now ({', '.join(now_) or '-'})"]
    if later:
        parts.append(f"{len(later)} queued ({', '.join(later)})")
    if shed:
        parts.append(f"{len(shed)} shed ({', '.join(shed)})")
    if lost:
        parts.append(f"{len(lost)} not placed ({', '.join(lost)})")
    parts.append(f"slots {'?' if found is None else len(trees)}/{cfg['slots']}")
    if story:
        parts.append(f"{sum(bool(TO_TEST.search(x[3])) for x in read_tests(story)[1])} wait on your testing")
    if found is None:
        parts.append("claude agents did not answer: no new chain starts until it does")
    return lines + [" · ".join(parts)]


def resolve_session(rows):
    """This session's id: ~/.claude/sessions/$CLAUDE_PID.json, else the `claude agents` row of the
    nearest parent pid (the Bash tool's shell is a child of the claude process, maybe nested)."""
    return self_session(rows)["sessionId"]


def self_session(rows):
    """This session's id, name and cwd, as resolve_session finds it; the sessions file needs no
    snapshot, so a launch tells a continuation even while `claude agents` is unknown."""
    try:
        data = json.loads((claude_dir() / "sessions" / f"{os.environ.get('CLAUDE_PID', '')}.json").read_text())
        if isinstance(data, dict) and data.get("sessionId"):
            return {"sessionId": data["sessionId"], "name": data.get("name") or "", "cwd": data.get("cwd") or ""}
    except (OSError, ValueError):
        pass
    by_pid = {r["pid"]: r for r in rows or [] if r.get("pid")}
    ps = subprocess.run(["ps", "-axo", "pid=,ppid="], capture_output=True, text=True).stdout
    parents = {int(a): int(b) for a, b in (line.split() for line in ps.splitlines() if len(line.split()) == 2)}
    pid = os.getppid()
    for _ in range(8):
        if pid in by_pid:
            row = by_pid[pid]
            return {"sessionId": row["sessionId"], "name": row.get("name") or "", "cwd": row.get("cwd") or ""}
        pid = parents.get(pid, 0)
        if pid <= 1:
            break
    return {"sessionId": None, "name": "", "cwd": ""}


def names_arg(values):
    return {n for v in values for n in re.split(r"[,\s]+", v) if n}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="flowkit-capacity", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("status", help="slots, chains and resources, one line each")
    p.add_argument("--json", action="store_true", help="print status.json instead")
    p = sub.add_parser("take", help="lease resources to this session, all or none")
    p.add_argument("names", nargs="+")
    p.add_argument("--session", help="the holder's session id (default: this session)")
    p.add_argument("--why", default="take")
    p = sub.add_parser("release", help="give back resources this session holds (all without names)")
    p.add_argument("names", nargs="*")
    who = p.add_mutually_exclusive_group()
    who.add_argument("--session")
    who.add_argument("--for", dest="for_", choices=[ME], help="give back the user's test hold")
    p = sub.add_parser("hold", help="the user's test hold")
    p.add_argument("names", nargs="+")
    p.add_argument("--for", dest="for_", choices=[ME], required=True)
    p.add_argument("--minutes", type=int, default=60)
    p = sub.add_parser("gate", help="flowkit-spawn's gate: launch, or queue (exit 11)")
    p.add_argument("--pid", required=True, help="the flowkit-spawn pid the launch holds for")
    p.add_argument("--tree", required=True)
    p.add_argument("--story", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--prompt", default="")
    p.add_argument("--over-cap", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--held", type=float, default=0, help="seconds the launch sat in a load hold first")
    p.add_argument("argv", nargs="*", help="after --: flowkit-spawn's arguments for a queued launch")
    p = sub.add_parser("launched", help="after flowkit-spawn: hand its holds to the new session, or give them back")
    p.add_argument("--pid", required=True)
    p.add_argument("--name", help="the session it started (it printed SPAWNED); without: give back")
    p = sub.add_parser("drain", help="launch every queued launch that can start now")
    p.add_argument("--closed", action="append", default=[], help="a session whose close is being recorded")
    p = sub.add_parser("waves", help="plan a batch: each item's wave, start and ready times, and the plan line")
    p.add_argument("items", nargs="+", metavar="NAME=TREE[:SLICES[:NEEDS]]")
    p.add_argument("--story", default="", help="the story whose test queue the plan line counts (and --write fills)")
    p.add_argument("--write", action="store_true", help="put every item that waits into the story's test queue")
    p = sub.add_parser("testq", help="a story's test queue, <story>.tests.md: list, set or drop a line")
    p.add_argument("story")
    p.add_argument("op", nargs="?", choices=["list", "set", "drop"], default="list")
    p.add_argument("item", nargs="?")
    p.add_argument("--mr")
    p.add_argument("--device")
    p.add_argument("--state")
    p.add_argument("--eta", help="HH:MM, or empty to clear")
    p.add_argument("--launch", help="the launch name whose drain moves this line to running")
    p = sub.add_parser("limit", help="a repo runs DELTA slots more or fewer for a while (0 ends it)")
    p.add_argument("repo")
    p.add_argument("delta", type=int)
    p.add_argument("--hours", type=float, default=12)
    args = parser.parse_args(argv)

    if args.cmd == "testq":
        t = now()
        if args.op != "list" and not args.item:
            parser.error(f"testq {args.op} needs an item")
        with lock():
            if args.op == "set":
                print(testq_set(args.story, args.item, t, mr=args.mr, device=args.device, state=args.state,
                                eta=None if args.eta is None else clock_arg(args.eta, t)))
                if args.launch:
                    tie_launch(args.launch, {"story": args.story, "item": args.item})
            elif args.op == "drop":
                if not testq_drop(args.story, args.item, t):
                    print(f"no line {args.item} in {tests_path(args.story)}", file=sys.stderr)
                    return 1
            else:
                for line in read_tests(args.story)[1]:
                    print(" · ".join(line).rstrip(" ·"))
        return 0
    if args.cmd == "limit":
        print(limit(args.repo, args.delta, args.hours, now()))
        return 0

    if args.cmd == "drain" and not queued():
        return 0  # an empty queue costs one directory read: no snapshot, no lock
    rows = snapshot()  # before the lock, never under it
    if args.cmd == "drain":
        for e in drain(rows, ending=set(args.closed)):
            print(f"launched #{e['id']} {e.get('name', '')} (flowkit-spawn pid {e['pid']})")
        return 0
    if args.cmd == "gate":
        code, msg = gate(rows, args.pid, args.tree, args.story, args.name, args.prompt, args.argv,
                         self_session(rows), args.over_cap, args.dry_run, held=args.held)
        print(msg)
        return code
    if args.cmd == "launched":
        code, msg = launched(rows, args.pid, args.name)
        print(msg, file=sys.stderr)
        drain(rows)
        return code
    if args.cmd == "waves":
        try:
            print("\n".join(waves(rows, args.items, args.story, args.write)))
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        return 0
    if args.cmd == "status":
        data = status(rows)
        print(json.dumps(data, indent=2) if args.json else "\n".join(status_lines(data)))
        return 0
    names = names_arg(getattr(args, "names", []))
    undeclared = names - declared(load_config())
    if undeclared:
        print(f"not declared in {config_path()}: {', '.join(sorted(undeclared))}", file=sys.stderr)
        return 2
    if args.cmd == "hold":
        code, msg = hold(rows, names, args.minutes)
    else:
        sid = ME if getattr(args, "for_", None) else args.session or resolve_session(rows)
        if not sid:
            print("this session is not listed by `claude agents`; pass --session <id>", file=sys.stderr)
            return 4
        code, msg = take(rows, sid, names, args.why) if args.cmd == "take" else release(rows, sid, names)
    print(msg, file=sys.stderr if code else sys.stdout)
    if args.cmd == "release":
        drain(rows)
    return code


if __name__ == "__main__":
    sys.exit(main())
