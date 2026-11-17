#!/usr/bin/env python3
"""The tmux kit copies this to ~/.local/scripts/capacity.py, where the tick of handoff 0.8.0 (its
copy of flowkit-tmux in ~/.local/bin) looks for capacity.py: it hands the call to the capacity.py
of the newest handoff in installed_plugins.json, so that tick drains the queue too."""
import json
import os
import sys


def version(root):
    try:
        with open(os.path.join(root, ".claude-plugin", "plugin.json")) as f:
            return tuple(int(n) for n in json.load(f)["version"].split("."))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


conf = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
try:
    with open(os.path.join(conf, "plugins", "installed_plugins.json")) as f:
        roots = [e["installPath"] for e in json.load(f)["plugins"]["handoff@flowkit"]]
except (OSError, ValueError, KeyError, TypeError):
    sys.exit("capacity: no handoff in installed_plugins.json")
found = [(v, r) for r in roots if os.path.isfile(os.path.join(r, "scripts", "capacity.py")) and (v := version(r))]
if not found:
    sys.exit("capacity: no installed handoff has scripts/capacity.py")
script = os.path.join(max(found)[1], "scripts", "capacity.py")
os.execv(sys.executable, [sys.executable, script, *sys.argv[1:]])
