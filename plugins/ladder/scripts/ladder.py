#!/usr/bin/env python3
"""The fixed ladder: a kind -> model/effort table in ~/.config/flowkit/ladder.md, the file a
handoff close reads to pick the next session's model and effort. The skills call this with
plain arguments; nothing here asks. A ladder.md this script did not write is never changed.

  ladder.py show                              the file, whether this plugin wrote it, the table
  ladder.py plan                              the user's plan (claude auth status) and its defaults
  ladder.py diff|write [--reset] [kind=model/effort ...] [max=on|off]
                                              the change as a diff, or write it; the base is the
                                              table there, or the plan's defaults (--reset, or
                                              no table yet)
  ladder.py gate                              PreToolUse hook on Bash: a max launch asks, once
                                              max is on (stdin: the hook input)
"""
import difflib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
CLAUDE = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
AWAY = CLAUDE / "away"
AWAY_HOURS = 12
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config") / "flowkit"
LADDER = CONFIG / "ladder.md"
MARKER = "<!-- flowkit-ladder -->"

KINDS = ["planning", "investigation", "implementation", "review", "docs", "config"]
EFFORTS = ["low", "medium", "high", "xhigh", "max"]
DEFAULTS = {
    "full": {
        "planning": "opus/xhigh",
        "investigation": "opus/high",
        "implementation": "opus/medium",
        "review": "opus/high",
        "docs": "sonnet/high",
        "config": "sonnet/high",
    },
    "light": {
        "planning": "opus/high",
        "investigation": "sonnet/xhigh",
        "implementation": "sonnet/high",
        "review": "sonnet/xhigh",
        "docs": "sonnet/high",
        "config": "sonnet/high",
    },
}
FULL_PLANS = {"max", "team", "enterprise"}

# A launch: flowkit-spawn, or `claude` as a command word (not ~/.claude/ paths, claude-* tools,
# or a backtick code span in prose); at effort max by flag or environment.
LAUNCH = re.compile(r"flowkit-spawn|(^|[^\w.`-])claude(\s|$)")
MAX = re.compile(r"(--effort[= ]+|CLAUDE_CODE_EFFORT_LEVEL=)([\"']?)max([^\w-]|$)")


def fail(msg, code=2):
    print(msg, file=sys.stderr)
    sys.exit(code)


def plan():
    """The subscriptionType `claude auth status` reports, or "unknown"."""
    try:
        out = subprocess.run(["claude", "auth", "status"], capture_output=True, text=True, timeout=20)
        return json.loads(out.stdout).get("subscriptionType") or "unknown"
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "unknown"


def defaults(name):
    return dict(DEFAULTS["full" if name in FULL_PLANS else "light"])


def read():
    """(text, managed, rows, max_on) of the ladder file; rows and max only from a managed one."""
    text = LADDER.read_text() if LADDER.is_file() else ""
    managed = text.startswith(MARKER)
    rows, max_on = {}, False
    if managed:
        for kind, model, effort in re.findall(r"^\|\s*([a-z]+)\s*\|\s*(\S+)\s*\|\s*([a-z]+)\s*\|$", text, re.M):
            if kind in KINDS:
                rows[kind] = f"{model}/{effort}"
        max_on = re.search(r"^max: on$", text, re.M) is not None
    return text, managed, rows, max_on


def render(rows, max_on):
    table = "\n".join(f"| {k} | {rows[k].split('/')[0]} | {rows[k].split('/')[1]} |" for k in KINDS if k in rows)
    return f"""{MARKER}
# Ladder

A fixed table, written by the `ladder` plugin: the next session's model and effort by its kind. Nothing here moves on its own. Change it with
`/ladder:edit`; a hand edit that breaks the table is read as no row.

## Rung

Take the row of the next session's kind. Its Model and Effort go into the prompt as they are,
and the Why line reads `<kind> on the fixed ladder`. A kind with no row runs at the user's
defaults: leave Model, Effort and Why out.

| Kind | Model | Effort |
|---|---|---|
{table}

max: {"on" if max_on else "off"}

## Close step

Nothing: this ladder logs nothing and moves nothing.
"""


def change(args):
    """The new file text for `diff`/`write`, from the current table or the plan's defaults."""
    text, managed, rows, max_on = read()
    if text and not managed:
        fail(f"{LADDER} was not written by the ladder plugin: it stays as it is. "
             "Move it away first to use the fixed table.", 3)
    if "--reset" in args or not rows:
        rows = defaults(plan())
    for arg in (a for a in args if a != "--reset"):
        key, _, value = arg.partition("=")
        if key == "max" and value in ("on", "off"):
            max_on = value == "on"
        elif key in KINDS and re.fullmatch(r"[a-z0-9][a-z0-9.\-\[\]]*/[a-z]+", value) and value.split("/")[1] in EFFORTS:
            rows[key] = value
        else:
            fail(f"not a ladder setting: {arg} (kinds: {', '.join(KINDS)}; efforts: {', '.join(EFFORTS)}; max=on|off)")
    if not max_on and any(v.endswith("/max") for v in rows.values()):
        fail("a row at effort max needs max=on first")
    return text, render(rows, max_on)


def gate():
    """Ask before a launch at effort max, only for a table this plugin wrote with max on; while
    the away flag is set, run it at xhigh instead. Everything else passes silently."""
    _, managed, _, max_on = read()
    if not (managed and max_on):
        return
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return
    cmd = (data.get("tool_input") or {}).get("command") or ""
    if not (LAUNCH.search(cmd) and MAX.search(cmd)):
        return
    out = {"hookEventName": "PreToolUse"}
    if AWAY.is_file() and time.time() - AWAY.stat().st_mtime < AWAY_HOURS * 3600:
        out.update(permissionDecision="allow",
                   permissionDecisionReason="You are away: the max launch runs at xhigh, (auto).",
                   updatedInput={**data["tool_input"], "command": MAX.sub(r"\1\2xhigh\3", cmd)})
    else:
        out.update(permissionDecision="ask",
                   permissionDecisionReason="Launches a Claude session at effort max, which needs your yes.")
    print(json.dumps({"hookSpecificOutput": out}))


def main(argv):
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    if cmd == "show" and not args:
        text, managed, rows, max_on = read()
        print(f"ladder: {LADDER if text else 'none'}")
        print(f"managed: {'yes' if managed else 'no' if text else '-'}")
        for k in KINDS:
            if k in rows:
                print(f"{k}: {rows[k]}")
        if managed:
            print(f"max: {'on' if max_on else 'off'}")
    elif cmd == "plan" and not args:
        name = plan()
        print(f"plan: {name}")
        for k, v in defaults(name).items():
            print(f"{k}: {v}")
    elif cmd in ("diff", "write"):
        before, after = change(args)
        if cmd == "diff":
            diff = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                                str(LADDER), str(LADDER)))
            print(diff or "unchanged")
        elif before == after:
            print(f"{LADDER}: unchanged")
        else:
            CONFIG.mkdir(parents=True, exist_ok=True)
            LADDER.write_text(after)
            print(f"{LADDER}: written")
    elif cmd == "gate" and not args:
        gate()
    else:
        fail(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
