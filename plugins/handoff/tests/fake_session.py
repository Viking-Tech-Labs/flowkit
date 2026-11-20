"""A stand-in for a Claude Code session in a tmux pane, for the tests and for a probe by hand: it
keeps its sessions file, shows an empty prompt box and takes the lines typed into it.

  fake_session.py <config dir> <session id> <name> [linked] [busy] [say:<a line above the prompt>]

`/remote-control` brings its Remote Control link back (bridgeSessionId), SIGUSR1 drops it (as an
account change does), `busy` and `idle` set its status, and every line it reads goes to
<config dir>/typed-<name>.log."""
import json
import os
import signal
import sys
from pathlib import Path

config, sid, name = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
info = {"sessionId": sid, "name": name, "kind": "interactive", "status": "busy" if "busy" in sys.argv[4:] else "idle",
        "bridgeSessionId": "cse_old" if "linked" in sys.argv[4:] else None}


def save():
    path = config / "sessions" / f"{os.getpid()}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix(".tmp").write_text(json.dumps(info))
    os.replace(path.with_suffix(".tmp"), path)


signal.signal(signal.SIGUSR1, lambda *_: (info.update(bridgeSessionId=None), save()))
save()
for arg in sys.argv[4:]:
    if arg.startswith("say:"):
        print(arg[4:])
while True:
    sys.stdout.write("\u276f ")
    sys.stdout.flush()
    line = sys.stdin.readline()
    if not line:
        break
    with (config / f"typed-{name}.log").open("a") as f:
        f.write(line)
    if line.strip() == "/remote-control":
        info["bridgeSessionId"] = "cse_new"
    elif line.strip() in ("busy", "idle"):
        info["status"] = line.strip()
    save()
