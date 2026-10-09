#!/usr/bin/env python3
"""Keep the rules a prompt audit produced in a section of your own in a CLAUDE.md.

  rules.py list [target]                    report the file, its managed blocks and the rules
                                            written so far; writes nothing
  rules.py add <rules.json> [target] [--replace]
                                            write the rules that are not there yet

rules.json is a list of {"id": "<lowercase-words-with-dashes>", "text": "<the rule, one line>"}.
Each rule becomes one bullet under the heading "## Rules from prompt audits", which is created
at the end of the file when missing:

  - <text> <!-- prompt-audit:<id> -->

A rule whose id or text is already in the file is not written again, so a second run adds
nothing. A rule whose id is there with another text is left as it is unless --replace is given.

A managed block is the text between "<!-- flowkit:<name> begin ... -->" and
"<!-- flowkit:<name> end -->". Nothing is ever written inside one, and every block is checked
to be byte-identical before the file is saved. target is a CLAUDE.md or a directory that holds
one; the default is CLAUDE.md in $CLAUDE_CONFIG_DIR, else in ~/.claude.

Exit codes: 0 done or nothing to do, 1 usage or unreadable input, 2 damaged block markers.
"""
import json
import os
import re
import sys

HEADING = "## Rules from prompt audits"
BEGIN = re.compile(r"<!-- flowkit:([\w-]+) begin([^>]*)-->")
END = re.compile(r"<!-- flowkit:([\w-]+) end -->")
RULE = re.compile(r"^[ \t]*- (.*?) <!-- prompt-audit:([a-z0-9-]+) -->[ \t]*\r?$", re.M)
SECTION = re.compile(rf"^{re.escape(HEADING)}[ \t]*\r?$", re.M)
NEXT = re.compile(r"^#{1,2} ", re.M)
ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


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


def blocks(text):
    """Return [(start, end, name, version)] for the managed blocks; exit 2 when markers are damaged."""
    begins = list(BEGIN.finditer(text))
    ends = list(END.finditer(text))
    paired = len(begins) == len(ends) and all(
        b.group(1) == e.group(1) and b.end() <= e.start() and (i == 0 or ends[i - 1].end() <= b.start())
        for i, (b, e) in enumerate(zip(begins, ends))
    )
    if not paired:
        where = ", ".join(
            [f"begin on line {line_of(text, m.start())}" for m in begins]
            + [f"end on line {line_of(text, m.start())}" for m in ends]
        )
        fail(
            f"The managed block markers are damaged ({where}): every begin marker needs its end marker after it. "
            "Nothing was written. Repair or delete the marker lines by hand, then run this again.",
            2,
        )
    return [(b.start(), e.end(), b.group(1), b.group(2).strip()) for b, e in zip(begins, ends)]


def outside(text, matches):
    spans = [(start, end) for start, end, _, _ in blocks(text)]
    return [m for m in matches if not any(start <= m.start() < end for start, end in spans)]


def rules_in(text):
    return outside(text, RULE.finditer(text))


def norm(rule_text):
    return " ".join(rule_text.split()).lower()


def load(path):
    try:
        with open(path, encoding="utf-8") as handle:
            rules = json.load(handle)
    except (OSError, ValueError) as error:
        fail(f"Cannot read the rules from {path}: {error}")
    if not isinstance(rules, list) or not all(isinstance(r, dict) for r in rules):
        fail(f"{path} must hold a list of objects with id and text.")
    cleaned = []
    for rule in rules:
        rule_id, text = str(rule.get("id", "")), " ".join(str(rule.get("text", "")).split())
        if not ID.fullmatch(rule_id):
            fail(f"Rule id {rule_id!r} must be lowercase words joined by dashes.")
        if not text or "-->" in text or "<!--" in text:
            fail(f"Rule {rule_id} needs a text of one line without a comment marker.")
        cleaned.append((rule_id, text))
    return cleaned


def insert(text, line):
    """Add one bullet at the end of the section, creating the section at the end of the file when missing."""
    newline = "\r\n" if "\r\n" in text else "\n"
    heads = outside(text, SECTION.finditer(text))
    if not heads:
        gap = "" if not text or text.endswith(newline * 2) else newline if text.endswith(newline) else newline * 2
        return text + gap + HEADING + newline * 2 + line + newline
    start = heads[0].end()
    stops = [m.start() for m in NEXT.finditer(text, start)] + [b[0] for b in blocks(text) if b[0] >= start]
    stop = min(stops) if stops else len(text)
    body = text[start:stop].rstrip()
    at = start + len(body)
    rest = text[at:]
    return text[:at] + (newline if body else newline * 2) + line + (rest or newline)


def add(path, rules, replace):
    original = read(path) or ""
    text = original
    for rule_id, rule_text in rules:
        line = f"- {rule_text} <!-- prompt-audit:{rule_id} -->"
        existing = rules_in(text)
        same_id = [m for m in existing if m.group(2) == rule_id]
        if same_id:
            m = same_id[0]
            if norm(m.group(1)) == norm(rule_text):
                print(f"unchanged  {rule_id}")
            elif replace:
                indent = re.match(r"[ \t]*", m.group(0)).group(0)
                tail = "\r" if m.group(0).endswith("\r") else ""
                text = text[:m.start()] + indent + line + tail + text[m.end():]
                print(f"replaced   {rule_id}")
            else:
                print(f"kept       {rule_id} (the file has another text for this id; --replace rewrites it)")
        elif any(norm(m.group(1)) == norm(rule_text) for m in existing):
            print(f"unchanged  {rule_id} (the same text is there under another id)")
        else:
            text = insert(text, line)
            print(f"added      {rule_id}")
    if text == original:
        print(f"Nothing written to {path}.")
        return
    before = [original[start:end] for start, end, _, _ in blocks(original)]
    after = [text[start:end] for start, end, _, _ in blocks(text)]
    if before != after:
        fail("Not written: the change would have touched a managed block. This is a defect in rules.py; please report it.")
    write(path, text)
    print(f"Wrote {path} under \"{HEADING}\"; no managed block was touched.")


def main():
    args = [a for a in sys.argv[1:] if a != "--replace"]
    replace = len(args) != len(sys.argv[1:])
    if not args or args[0] not in ("list", "add") or len(args) > (2 if args[0] == "list" else 3) or args[:1] == ["add"] and len(args) < 2:
        fail(__doc__)
    if args[0] == "add":
        add(target_path(args[2] if len(args) == 3 else None), load(args[1]), replace)
        return
    path = target_path(args[1] if len(args) == 2 else None)
    text = read(path)
    print(f"file     {path}" + ("" if text is not None else " (does not exist)"))
    text = text or ""
    for start, end, name, version in blocks(text):
        print(f"managed  flowkit:{name} {version}, lines {line_of(text, start)}-{line_of(text, end)} (never written to)")
    found = rules_in(text)
    print(f"rules    {len(found)} under \"{HEADING}\"" if found else "rules    none yet")
    for m in found:
        print(f"  {m.group(2)}: {m.group(1)}")


if __name__ == "__main__":
    main()
