#!/usr/bin/env python3
"""flowkit capacity: how many chains this machine runs at once, and who holds each named resource.

Slots are counted when asked, from `claude agents` and the story files, so nothing has to be given
back. Resources (a simulator, a dev-server port) are leased to a session id and go free with the
holder's recorded close, its death or half an hour idle. The numbers live in
`${XDG_CONFIG_HOME:-~/.config}/flowkit/capacity.conf`; with no file a machine runs 3 chains and
declares no resources. State lives under `${CLAUDE_CONFIG_DIR:-~/.claude}/capacity/`."""
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULTS = {"slots": 3, "slice_minutes": 50, "coordinator_growth": 300_000}
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
