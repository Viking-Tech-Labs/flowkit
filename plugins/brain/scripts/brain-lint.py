#!/usr/bin/env python3
"""Deterministic lint for a brain vault: every /brain:lint check that needs no judgement.

Reads wiki/, index.md and log.md and prints what is off. It changes nothing: fixes are
reported and asked about.

  index drift    a wiki page with no index.md entry, or an entry with no page
  dangling       [[links]] to a page that does not exist (code spans and fences are skipped)
  orphans        pages no other wiki page links to, minus the slugs listed in <vault>/.brain-hubs
  frontmatter    title, tags, created, updated present; the two dates valid
  log.md         entries in date order, each one `- YYYY-MM-DD:` or `- YYYY-MM-DD (qualifier):`
  provenance     runbooks with neither a `**Confirmed working <date>**` stamp nor
                 "Not yet human-verified", and stamps over 180 days old
  symptoms       runbook / troubleshooting pages without `symptoms:` or `## Symptoms`
  stale          pages whose `updated:` is 90 or more days back

  brain-lint.py [path]    lint the vault at path; default $BRAIN_DIR, else the current directory

.brain-hubs is optional: one slug per line, `#` starts a comment. Hubs are pages that stand
alone by design (indexed entry points nothing else needs to link to).

Exit 1 when a defect is found; orphans, old stamps and stale pages are for a human look
and leave the exit at 0. Run by /brain:lint.
"""
import datetime
import glob
import os
import re
import sys

FIELDS = ("title", "tags", "created", "updated")
LINK = re.compile(r"\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
DATED = re.compile(r"(?:- )?(\d{4}-\d{2}-\d{2})")
ENTRY = re.compile(r"- \d{4}-\d{2}-\d{2}(?: \([^)]+\))?:")
STAMP = re.compile(r"\*\*Confirmed working (\d{4}-\d{2}-\d{2})\*\*")
STAMP_DAYS, STALE_DAYS = 180, 90


def read(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def hubs(brain):
    path = os.path.join(brain, ".brain-hubs")
    if not os.path.exists(path):
        return set()
    return {line.split("#")[0].strip() for line in read(path).split("\n") if line.split("#")[0].strip()}


def links(text):
    """(line number, target) for every [[link]] outside fenced blocks and inline code."""
    found, fenced = [], False
    for number, line in enumerate(text.split("\n"), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if fenced:
            continue
        for target in LINK.findall(re.sub(r"(`+).+?\1", "", line)):
            found.append((number, target.strip()))
    return found


def frontmatter(text):
    block = re.match(r"---\n(.*?)\n---", text, re.S)
    if not block:
        return None
    fields, key = {}, None
    for line in block.group(1).split("\n"):
        pair = re.match(r"(\w+):\s*(.*)", line)
        if pair:
            key = pair.group(1)
            fields[key] = pair.group(2).strip()
        elif key:
            fields[key] = f"{fields[key]} {line.strip()}".strip()
    return fields


def day(value):
    try:
        return datetime.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def section(title, lines):
    print(f"{title}: {len(lines) or 'ok'}")
    for line in lines:
        print(f"  {line}")


def main():
    brain = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.environ.get("BRAIN_DIR") or os.getcwd())
    today = datetime.date.today()
    absent = [part for part in ("wiki", "index.md", "log.md") if not os.path.exists(os.path.join(brain, part))]
    if absent:
        print(f"{brain} is not a vault: missing {', '.join(absent)}")
        return 1
    pages = {os.path.basename(path)[:-3]: read(path) for path in sorted(glob.glob(os.path.join(brain, "wiki", "*.md")))}
    index = read(os.path.join(brain, "index.md"))
    log = read(os.path.join(brain, "log.md"))
    print(f"brain lint {today} ({brain}): {len(pages)} pages\n")

    entries = set(re.findall(r"^- \[\[([^\]|#]+)", index, re.M))
    drift = [f"wiki/{name}.md has no index.md entry" for name in pages if name not in entries]
    drift += [f"index.md lists [[{name}]], no such page" for name in sorted(entries) if name not in pages]
    section("index drift", drift)

    dangling, inbound = [], {name: set() for name in pages}
    for name, text in pages.items():
        for number, target in links(text):
            if target in pages:
                if target != name:
                    inbound[target].add(name)
            else:
                dangling.append(f"wiki/{name}.md:{number} [[{target}]]")
    for name in ("index.md", "_me.md", "CLAUDE.md", "hot.md"):
        path = os.path.join(brain, name)
        if os.path.exists(path):
            dangling += [f"{name}:{number} [[{target}]]" for number, target in links(read(path))
                         if target not in pages and target not in entries]
    section("dangling links", dangling)

    standalone = hubs(brain)
    orphans = [f"wiki/{name}.md" for name in pages if not inbound[name] and name not in standalone]
    section("orphans (no other wiki page links in)", orphans)

    meta = {name: frontmatter(text) for name, text in pages.items()}
    broken = []
    for name, fields in meta.items():
        if fields is None:
            broken.append(f"wiki/{name}.md has no frontmatter")
            continue
        wrong = [f"no {field}" for field in FIELDS if not fields.get(field)]
        wrong += [f"{field} is not a date" for field in ("created", "updated") if fields.get(field) and not day(fields[field])]
        if wrong:
            broken.append(f"wiki/{name}.md: {', '.join(wrong)}")
    section("frontmatter", broken)

    order, shape, last = [], [], ""
    for number, line in enumerate(log.split("\n"), 1):
        dated = DATED.match(line)
        if dated:
            if dated.group(1) < last:
                order.append(f"log.md:{number} {dated.group(1)} comes after {last}")
            last = dated.group(1)
        if not ENTRY.match(line) and (dated or line.startswith("- ") or (last and line.strip())):
            shape.append(f"log.md:{number} {line[:60]}")
    section("log.md out of order", order)
    section("log.md entries off format", shape)

    bare, aged, lacking, counts = [], [], [], {"stamped": 0, "not verified": 0}
    for name, text in pages.items():
        fields = meta[name] or {}
        tags = re.findall(r"[\w-]+", fields.get("tags", ""))
        if "runbook" in tags:
            stamps = [day(stamp) for stamp in STAMP.findall(text)]
            unverified = "not yet human-verified" in text.lower()
            counts["stamped"] += bool(stamps)
            counts["not verified"] += unverified and not stamps
            if not stamps and not unverified:
                bare.append(f"wiki/{name}.md")
            aged += [f"wiki/{name}.md stamped {stamp}, {(today - stamp).days} days ago"
                     for stamp in stamps if stamp and (today - stamp).days > STAMP_DAYS]
        if "runbook" in tags or "troubleshooting" in tags:
            missing = [what for what, there in (("symptoms: frontmatter", fields.get("symptoms")),
                                                ("## Symptoms section", re.search(r"^## Symptoms", text, re.M))) if not there]
            if missing:
                lacking.append(f"wiki/{name}.md: no {', no '.join(missing)}")
    section('runbooks with no stamp and no "Not yet human-verified"', bare)
    section(f"runbook stamps over {STAMP_DAYS} days (human re-confirm)", aged)
    section("runbook / troubleshooting pages missing symptoms", lacking)

    stale = []
    for name, fields in meta.items():
        updated = day((fields or {}).get("updated"))
        if updated and (today - updated).days >= STALE_DAYS:
            stale.append(f"wiki/{name}.md updated {updated}, {(today - updated).days} days ago")
    section(f"stale, not updated for {STALE_DAYS}+ days", stale)

    defects = len(drift) + len(dangling) + len(broken) + len(order) + len(shape) + len(bare) + len(lacking)
    print(f"\nsummary: {defects} defects (index {len(drift)}, dangling {len(dangling)}, frontmatter {len(broken)}, "
          f"log order {len(order)}, log format {len(shape)}, runbooks unlabeled {len(bare)}, symptoms {len(lacking)}) · "
          f"to look at: orphans {len(orphans)}, old stamps {len(aged)}, stale {len(stale)} · "
          f"runbooks: {counts['stamped']} stamped, {counts['not verified']} not yet human-verified")
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main())
