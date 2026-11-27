#!/usr/bin/env python3
"""Status line that records each session's context use and cost, and the plan's usage windows.

Reads Claude Code's status line JSON on stdin and writes, under ~/.claude (CLAUDE_CONFIG_DIR):

  session-stats/<session id>.json           the session, condensed (and by-name/<session name>.json
                                            when it has a name): for closes and ladders
  usage-stats/week-<reset>[-<login>].json   one file per weekly window of a login, holding the
                                            highest figures its sessions reported: what
                                            flowkit-usage falls back to
  usage-stats/series.jsonl                  one line each time such a file changed

Then prints a short line: model, effort, context used, cost. With `--then <command>`, it runs that
command (the user's own status line) on the same input and prints its output instead.

Every session of a login reports the same windows, and an idle one keeps reporting the figures of
its last API call. So a week file keeps the highest percentage seen, and only a session that made
an API call since its previous refresh can say the limit was reset (a reading 5 points or more
under the stored one) or raise the figure after such a reset.

The usage half began in September 2026 as a private status line, kept to see afterwards when a
week ran out and how fast; rewritten here in November 2026 so one writer serves every reader.
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
STATS = CLAUDE / "session-stats"
USAGE = CLAUDE / "usage-stats"
RESET_DROP = 5


def read(path):
    try:
        found = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def record(data, stamp):
    """Writes the session's file. True when the session made an API call since its last refresh."""
    sid = data.get("session_id")
    if not sid:
        return False
    cost, ctx = data.get("cost") or {}, data.get("context_window") or {}
    stats = {
        "session_id": sid,
        "session_name": data.get("session_name"),
        "model_id": (data.get("model") or {}).get("id"),
        "effort": (data.get("effort") or {}).get("level"),
        "cost_usd": cost.get("total_cost_usd"),
        "api_ms": cost.get("total_api_duration_ms"),
        "wall_ms": cost.get("total_duration_ms"),
        "ctx_pct": ctx.get("used_percentage"),
        "ctx_size": ctx.get("context_window_size"),
        "lines_added": cost.get("total_lines_added"),
        "lines_removed": cost.get("total_lines_removed"),
        "cwd": (data.get("workspace") or {}).get("current_dir"),
        "updated": stamp,
    }
    before = read(STATS / f"{sid}.json")
    line = json.dumps(stats, separators=(",", ":")) + "\n"
    (STATS / "by-name").mkdir(parents=True, exist_ok=True)
    (STATS / f"{sid}.json").write_text(line)
    name = stats["session_name"]
    if name:
        (STATS / "by-name" / f"{name.replace('/', '-')}.json").write_text(line)
    return bool(before) and stats["api_ms"] is not None and before.get("api_ms") != stats["api_ms"]


def login():
    """The account and organization ids this session runs on, as flowkit-usage names them: read
    from the account file its environment points at. None without a login."""
    try:
        who = json.loads((Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home()) / ".claude.json").read_text())
        who = who.get("oauthAccount")
    except (OSError, ValueError, AttributeError):
        return None
    return {"account": who["accountUuid"], "organization": who.get("organizationUuid")} \
        if isinstance(who, dict) and who.get("accountUuid") else None


def week_figures(old, week, five, five_ends, fresh, stamp):
    """The week file's figures after one reading: `week` and `five` are the two percentages a
    session reports, `five_ends` the end of its 5-hour window."""
    new = {k: old.get(k) for k in ("seven_day_pct", "five_hour_pct", "five_hour_resets_at", "five_hour_peak_pct",
                                   "full_at", "reset_at", "pre_reset_pct")}
    held = number(old.get("seven_day_pct"))
    if week is not None:
        if held is None:
            new["seven_day_pct"] = week
        elif fresh and week <= held - RESET_DROP:
            new.update(seven_day_pct=week, reset_at=stamp, pre_reset_pct=held)
        elif fresh or not old.get("reset_at"):
            new["seven_day_pct"] = max(week, held)
    if new["seven_day_pct"] is not None and new["seven_day_pct"] >= 100 and not new["full_at"]:
        new["full_at"] = stamp
    if five is not None:
        new["five_hour_peak_pct"] = max(five, number(old.get("five_hour_peak_pct")) or 0)
        window = number(old.get("five_hour_resets_at"))
        if five_ends is not None and (window is None or five_ends > window):
            new.update(five_hour_pct=five, five_hour_resets_at=five_ends)
        elif five_ends is not None and five_ends == window:
            new["five_hour_pct"] = max(five, number(old.get("five_hour_pct")) or 0)
    return new


def record_usage(data, fresh, stamp):
    limits = data.get("rate_limits") or {}
    week, five = limits.get("seven_day") or {}, limits.get("five_hour") or {}
    reset, five_ends = number(week.get("resets_at")), number(five.get("resets_at"))
    if reset is None:
        return
    who = login()
    key = "-" + hashlib.sha1(json.dumps(who, sort_keys=True).encode()).hexdigest()[:12] if who else ""
    path = USAGE / f"week-{int(reset)}{key}.json"
    old = read(path)
    new = {"resets_at": int(reset), "account": who,
           **week_figures(old, number(week.get("used_percentage")), number(five.get("used_percentage")),
                          None if five_ends is None else int(five_ends), fresh, stamp)}
    if new == {k: v for k, v in old.items() if k != "updated"}:
        return
    USAGE.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f".{path.name}.{os.getpid()}")
    try:
        part.write_text(json.dumps({**new, "updated": stamp}, separators=(",", ":")) + "\n")
        os.replace(part, path)
    finally:
        if part.exists():
            part.unlink()
    sample = {"t": stamp, **{k: new[k] for k in ("resets_at", "account", "seven_day_pct", "five_hour_pct",
                                                  "five_hour_resets_at")},
              "session_id": data.get("session_id"), "model_id": (data.get("model") or {}).get("id"),
              "cost_usd": (data.get("cost") or {}).get("total_cost_usd")}
    with open(USAGE / "series.jsonl", "a") as series:
        series.write(json.dumps(sample, separators=(",", ":")) + "\n")


def line(data):
    model = (data.get("model") or {}).get("display_name") or ""
    effort = (data.get("effort") or {}).get("level") or ""
    pct = (data.get("context_window") or {}).get("used_percentage")
    cost = (data.get("cost") or {}).get("total_cost_usd")
    parts = [" ".join(p for p in (model, effort) if p)]
    parts.append(f"ctx {pct:.0f}%" if isinstance(pct, (int, float)) else "ctx idle")
    if isinstance(cost, (int, float)):
        parts.append(f"${cost:.2f}")
    return " · ".join(p for p in parts if p)


def main(argv):
    raw = sys.stdin.read()
    try:
        data = json.loads(raw)
    except ValueError:
        data = {}
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    fresh = False
    try:
        fresh = record(data, stamp)
    except OSError:
        pass
    try:
        record_usage(data, fresh, stamp)
    except (OSError, AttributeError, TypeError):
        pass
    if len(argv) == 2 and argv[0] == "--then":
        out = subprocess.run(argv[1], shell=True, input=raw, capture_output=True, text=True)
        sys.stdout.write(out.stdout)
    else:
        print(line(data))


if __name__ == "__main__":
    main(sys.argv[1:])
