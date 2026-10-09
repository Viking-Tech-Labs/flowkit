#!/usr/bin/env python3
"""Manage the session-conventions block in a CLAUDE.md.

  adopt.py status  <plugin-root> <data-dir> [target]   report the block's state and the flowkit
                                                       plugins installed and available; writes nothing
  adopt.py install <plugin-root> <data-dir> [target]   append the block when the file has none
                                                       (the file is created when missing)
  adopt.py diff    <plugin-root> <data-dir> [target]   show what update would change; writes nothing
  adopt.py update  <plugin-root> <data-dir> [target] [--overwrite-edits]
                                                       rewrite the block in place; a block edited
                                                       since it was written needs --overwrite-edits

The block is the text of <plugin-root>/block/conventions.md between two marker comments:

  <!-- flowkit:session-discipline begin v<version> -->
  <!-- flowkit:session-discipline end -->

Nothing outside the markers is ever changed. target is a CLAUDE.md or a directory that holds
one; the default is CLAUDE.md in $CLAUDE_CONFIG_DIR, else in ~/.claude. <data-dir> keeps a
checksum of each block as written, which is how a later edit by hand is noticed.

Exit codes: 0 done or nothing to do, 1 usage or unreadable input, 2 damaged markers,
3 update refused because the block was edited by hand.
"""
import difflib
import hashlib
import json
import os
import re
import subprocess
import sys

MARKETPLACE = "flowkit"
NAME = f"{MARKETPLACE}:session-discipline"
BEGIN = re.compile(rf"<!-- {re.escape(NAME)} begin v(\S+) -->")
END = re.compile(rf"<!-- {re.escape(NAME)} end -->")
STATE_FILE = "adopted-blocks.json"


def fail(message, code=1):
    print(message, file=sys.stderr)
    sys.exit(code)


def target_path(argument):
    if argument:
        path = os.path.abspath(os.path.expanduser(argument))
        if os.path.isdir(path) or not os.path.basename(path).lower().endswith(".md"):
            path = os.path.join(path, "CLAUDE.md")
        return path
    config = os.environ.get("CLAUDE_CONFIG_DIR", "").strip() or "~/.claude"
    return os.path.join(os.path.abspath(os.path.expanduser(config)), "CLAUDE.md")


def available(plugin_root):
    with open(os.path.join(plugin_root, ".claude-plugin", "plugin.json"), encoding="utf-8") as handle:
        version = json.load(handle)["version"]
    with open(os.path.join(plugin_root, "block", "conventions.md"), encoding="utf-8") as handle:
        body = handle.read().strip("\n")
    return version, body


def render(version, body):
    return f"<!-- {NAME} begin v{version} -->\n{body}\n<!-- {NAME} end -->"


def read(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8", newline="") as handle:
        return handle.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def line_of(text, offset):
    return text.count("\n", 0, offset) + 1


def find(text):
    """Return (start, end, version, body) of the block, None when there is none; exit 2 when damaged."""
    begins = list(BEGIN.finditer(text))
    ends = list(END.finditer(text))
    if not begins and not ends:
        return None
    if len(begins) != 1 or len(ends) != 1 or ends[0].start() < begins[0].end():
        where = ", ".join(
            [f"begin on line {line_of(text, m.start())}" for m in begins]
            + [f"end on line {line_of(text, m.start())}" for m in ends]
        )
        fail(
            f"The markers are damaged ({where}): there must be exactly one begin marker with one end "
            "marker after it. Nothing was written. Repair or delete the marker lines by hand, then run this again.",
            2,
        )
    body = text[begins[0].end():ends[0].start()].replace("\r\n", "\n").strip("\n")
    return begins[0].start(), ends[0].end(), begins[0].group(1), body


def digest(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def load_state(data_dir):
    try:
        with open(os.path.join(data_dir, STATE_FILE), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def record(data_dir, path, version, body):
    state = load_state(data_dir)
    state[path] = {"version": version, "sha256": digest(body)}
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, STATE_FILE), "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)
        handle.write("\n")


def edited(data_dir, path, body, new_body):
    """True, False, or None when there is no record of what was written."""
    if body == new_body:
        return False
    written = load_state(data_dir).get(path)
    return None if not written else written["sha256"] != digest(body)


def changes(body, new_body):
    return "\n".join(
        difflib.unified_diff(body.split("\n"), new_body.split("\n"), "in your file", "in the plugin", lineterm="", n=0)
    )


def describe(data_dir, path, found, version, body):
    if found is None:
        return "no block"
    _, _, have, have_body = found
    state = "current" if (have, have_body) == (version, body) else f"differs from the plugin's v{version}"
    hand = edited(data_dir, path, have_body, body)
    if hand:
        state += ", edited by hand since it was written"
    elif hand is None:
        state += ", text differs and there is no record of what was written (treated as edited by hand)"
    return f"block v{have}, {state}"


def tiers():
    try:
        out = subprocess.run(
            ["claude", "plugin", "list", "--json", "--available"], capture_output=True, text=True, timeout=60
        )
        listing = json.loads(out.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ["could not read `claude plugin list --json --available`"]
    lines = []
    for plugin in listing.get("installed", []):
        if plugin.get("id", "").endswith("@" + MARKETPLACE):
            enabled = "" if plugin.get("enabled", True) else ", disabled"
            lines.append(f"installed  {plugin['id']} {plugin.get('version', '?')} ({plugin.get('scope', '?')} scope{enabled})")
    for plugin in listing.get("available", []):
        if plugin.get("marketplaceName") == MARKETPLACE:
            lines.append(f"available  {plugin['pluginId']}: {plugin.get('description', '')}")
    return lines or [f"no plugin of the {MARKETPLACE} marketplace is listed"]


def main():
    args = [a for a in sys.argv[1:] if a != "--overwrite-edits"]
    overwrite = len(args) != len(sys.argv[1:])
    if len(args) not in (3, 4) or args[0] not in ("status", "install", "diff", "update"):
        fail(__doc__)
    command, plugin_root, data_dir = args[:3]
    path = target_path(args[3] if len(args) == 4 else None)
    version, body = available(plugin_root)
    text = read(path)
    found = find(text) if text is not None else None

    if command == "status":
        print(f"file       {path}" + ("" if text is not None else " (does not exist)"))
        print(f"block      {describe(data_dir, path, found, version, body)}; the plugin has v{version}")
        print("\n".join(tiers()))
    elif command == "install":
        if found:
            print(f"{path} already has the block ({describe(data_dir, path, found, version, body)}). Nothing written.")
            return
        text = text or ""
        newline = "\r\n" if "\r\n" in text else "\n"
        gap = "" if not text or text.endswith(newline * 2) else newline if text.endswith(newline) else newline * 2
        write(path, text + gap + render(version, body).replace("\n", newline) + newline)
        record(data_dir, path, version, body)
        print(f"Wrote the block v{version} at the end of {path}" + ("." if text else " (file created)."))
    else:
        if not found:
            print(f"{path} has no block. Nothing to update; install it first.")
            return
        start, end, have, have_body = found
        if (have, have_body) == (version, body):
            print(f"The block in {path} is already v{version} with the plugin's text. Nothing written.")
            return
        print(f"version: v{have} in your file, v{version} in the plugin")
        print(changes(have_body, body) or "text: identical")
        if command == "diff":
            return
        if edited(data_dir, path, have_body, body) is not False and not overwrite:
            fail(
                "Not written: the block's text was edited by hand (or there is no record of what was written), "
                "and an update replaces it with the plugin's text. The lines marked - above would be lost. "
                "Run update again with --overwrite-edits to accept that.",
                3,
            )
        newline = "\r\n" if "\r\n" in text else "\n"
        write(path, text[:start] + render(version, body).replace("\n", newline) + text[end:])
        record(data_dir, path, version, body)
        print(f"Rewrote the block in {path} between its markers; nothing outside them changed.")


if __name__ == "__main__":
    main()
