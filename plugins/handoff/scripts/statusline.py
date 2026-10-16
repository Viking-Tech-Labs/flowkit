#!/usr/bin/env python3
"""Status line that records each session's context use and cost, for closes and ladders.

Reads Claude Code's status line JSON on stdin and writes it, condensed, to
~/.claude/session-stats/<session id>.json (and by-name/<session name>.json when the session has
a name). Then prints a short line: model, effort, context used, cost. With `--then <command>`, it
runs that command (the user's own status line) on the same input and prints its output instead.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

STATS = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "session-stats"


def record(data):
    sid = data.get("session_id")
    if not sid:
        return
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
        "lines_added": cost.get("total_lines_added"),
        "lines_removed": cost.get("total_lines_removed"),
        "cwd": (data.get("workspace") or {}).get("current_dir"),
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    line = json.dumps(stats, separators=(",", ":")) + "\n"
    (STATS / "by-name").mkdir(parents=True, exist_ok=True)
    (STATS / f"{sid}.json").write_text(line)
    name = stats["session_name"]
    if name:
        (STATS / "by-name" / f"{name.replace('/', '-')}.json").write_text(line)


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
    try:
        record(data)
    except OSError:
        pass
    if len(argv) == 2 and argv[0] == "--then":
        out = subprocess.run(argv[1], shell=True, input=raw, capture_output=True, text=True)
        sys.stdout.write(out.stdout)
    else:
        print(line(data))


if __name__ == "__main__":
    main(sys.argv[1:])
