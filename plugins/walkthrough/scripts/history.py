#!/usr/bin/env python3
"""Keep the history of your walkthroughs, and the checkout each project reads its other side from.

  history.py where <data-dir> [project-dir]   print where the history lives and, for the project,
                                              the configured backend checkout; writes nothing
  history.py record <data-dir> --id ID --date YYYY-MM-DD --project NAME --target TEXT
                     [--questions N --correct N --misses N --disagreements N]
                     [--pattern TEXT] [--friction TEXT]
                                              write one run: a row in the runs table and, when the
                                              run has friction notes, a bullet under Friction
  history.py runs <data-dir>                  print the runs recorded so far
  history.py backend <data-dir> <project-dir> [<path> | --clear]
                                              show, set or clear the checkout that holds the other
                                              side of the project's API

The history is one page. With a brain vault it is wiki/walkthrough-runs.md there, added to the
vault's index.md and log.md when the page is created, and when the vault is a git repository
the files the run wrote are committed there, those files only, never pushed. The vault is $BRAIN_DIR, else the path in
the file vault-path of the brain plugin's data directory beside <data-dir>; it counts when it
holds wiki/ and index.md. With no vault the page is <data-dir>/runs.md.

A run is its id (lowercase words joined by dashes), the date, the project's name, the target (a
branch, a pull request or "pasted diff"), the four counts (0 when left out), the miss pattern in a
few words, and optionally the friction: what in the skill was unclear, missing or wrong.

A run nobody could answer has --questions 0 and is written as "not asked". Each row and bullet
ends in a comment with the run's id. A run whose id is already on the page replaces its own row
and bullet, so recording the same run twice changes nothing. Everything else on the page is
yours: the script only touches lines that carry a run id, the `updated:` date, and the two
headings when they are missing. The page holds scores and patterns, never questions or answers,
so a later walkthrough of the same code cannot read its own answer key.

<data-dir>/backends.json maps a project (the top level of its git repository) to the checkout
set with `backend`. The checkout is only ever read.

Exit codes: 0 done, 1 usage or unreadable input.
"""
import datetime
import json
import os
import re
import subprocess
import sys

PLUGIN = "walkthrough"
SLUG = "walkthrough-runs"
BACKENDS_FILE = "backends.json"
RUNS, FRICTION = "## Runs", "## Friction"
HEADER = ("| Date | Project | Target | Questions | Correct / miss / disagreement | Miss pattern |\n"
          "|---|---|---|---|---|---|\n")
INTRO = ("One row per walkthrough: what was walked, how the predictions went and which kind of mechanism\n"
         "was misjudged. Scores and patterns only, never the questions or their answers. The rows and the\n"
         "friction bullets are written by the `walkthrough` plugin; the rest of the page is yours.\n")
INDEX_LINE = f"- [[{SLUG}]] - one row per code walkthrough: scores and the miss pattern"
ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
NEXT = re.compile(r"^#{1,2} ", re.M)
TEXTS = ("id", "date", "project", "target", "pattern", "friction")
COUNTS = ("questions", "correct", "misses", "disagreements")


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def absolute(path):
    return os.path.abspath(os.path.expanduser(path))


def vault(data_dir):
    """The vault of the brain plugin when one resolves."""
    candidates = [os.environ.get("BRAIN_DIR", "").strip()]
    name = os.path.basename(os.path.normpath(data_dir))
    if name.startswith(PLUGIN):
        path_file = os.path.join(os.path.dirname(os.path.normpath(data_dir)), "brain" + name[len(PLUGIN):], "vault-path")
        try:
            with open(path_file, encoding="utf-8") as handle:
                candidates.append(handle.read().strip())
        except OSError:
            pass
    for candidate in candidates:
        if candidate and os.path.isdir(os.path.join(absolute(candidate), "wiki")) and os.path.exists(os.path.join(absolute(candidate), "index.md")):
            return absolute(candidate)
    return None


def page(data_dir):
    home = vault(data_dir)
    return (os.path.join(home, "wiki", SLUG + ".md"), home) if home else (os.path.join(data_dir, "runs.md"), None)


def read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def git(directory, *arguments):
    try:
        done = subprocess.run(["git", "-C", directory, *arguments], capture_output=True, text=True)
    except OSError:
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def project_key(directory):
    directory = os.path.realpath(absolute(directory))
    return git(directory, "rev-parse", "--show-toplevel") or directory


def load_backends(data_dir):
    try:
        with open(os.path.join(data_dir, BACKENDS_FILE), encoding="utf-8") as handle:
            found = json.load(handle)
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) else {}


def describe_backend(path):
    if not os.path.isdir(path):
        return f"{path} (missing: not a directory any more)"
    branch, commit = git(path, "rev-parse", "--abbrev-ref", "HEAD"), git(path, "rev-parse", "--short", "HEAD")
    return f"{path} (branch {branch}, commit {commit})" if branch and commit else f"{path} (not a git checkout)"


def cell(value):
    return " ".join(str(value).replace("|", "/").split())


def section(text, heading):
    """(start of the section's body, its end), or None when the heading is not on the page."""
    found = re.search(rf"^{re.escape(heading)}[ \t]*$", text, re.M)
    if not found:
        return None
    start = min(found.end() + 1, len(text))
    following = NEXT.search(text, start)
    return start, following.start() if following else len(text)


def put(text, heading, marker, line, lead=""):
    """Set the line that carries marker under heading: replaced in place, else added after the last one."""
    if section(text, heading) is None:
        text = text.rstrip("\n") + f"\n\n{heading}\n\n{lead}"
    start, end = section(text, heading)
    lines = text[start:end].split("\n")
    kept = [index for index, existing in enumerate(lines) if marker in existing]
    if kept:
        if line is None:
            del lines[kept[0]]
        else:
            lines[kept[0]] = line
    elif line is not None:
        filled = [index for index, existing in enumerate(lines) if existing.strip()]
        if filled:
            lines.insert(filled[-1] + 1, line)
        else:
            lines = ["", line, ""]
        if lines[-1].strip():
            lines.append("")
    return text[:start] + "\n".join(lines) + text[end:]


def new_page(today, in_vault):
    front = f"---\ntitle: Walkthrough runs\ntags: [tool]\ncreated: {today}\nupdated: {today}\n---\n\n" if in_vault else "# Walkthrough runs\n\n"
    return f"{front}{INTRO}\n{RUNS}\n\n{HEADER}\n{FRICTION}\n"


def register(home, today):
    """A new page in a vault gets its index.md line and a log.md entry."""
    index_path = os.path.join(home, "index.md")
    index = read(index_path) or ""
    if f"[[{SLUG}]]" not in index:
        if section(index, "## tools") is None:
            index = index.rstrip("\n") + "\n\n## tools\n"
        write(index_path, put(index, "## tools", "\0", INDEX_LINE))
    log_path = os.path.join(home, "log.md")
    log = read(log_path)
    if log is not None:
        write(log_path, log.rstrip("\n") + f"\n- {today}: created {SLUG}, the history of code walkthroughs\n")


def commit(home, paths, run_id):
    """Commit the files this run wrote, and only those, when the vault is a git repository."""
    if git(home, "rev-parse", "--show-toplevel") is None:
        return "vault is not a git repository: nothing to commit"
    paths = [path for path in paths if os.path.exists(path)]
    if git(home, "add", "--", *paths) is None or git(home, "commit", "-q", "-m", f"Record walkthrough run {run_id}", "--", *paths) is None:
        return "not committed: git refused; commit " + ", ".join(os.path.relpath(path, home) for path in paths) + " in the vault yourself"
    return "committed in the vault (not pushed): " + ", ".join(os.path.relpath(path, home) for path in paths)


def load_run(arguments):
    run = {}
    if len(arguments) % 2:
        fail("record takes pairs of --name value")
    for name, value in zip(arguments[::2], arguments[1::2]):
        if name[:2] != "--" or name[2:] not in TEXTS + COUNTS:
            fail(f"record does not know {name}")
        run[name[2:]] = value
    if not ID.fullmatch(run.get("id", "")):
        fail("the run needs an --id of lowercase words joined by dashes")
    if not DATE.fullmatch(run.get("date", "")):
        fail("the run needs a --date as YYYY-MM-DD")
    for field in ("project", "target"):
        if not run.get(field, "").strip():
            fail(f"the run needs a --{field}")
    for field in COUNTS:
        if not run.setdefault(field, "0").isdigit():
            fail(f"--{field} must be a whole number")
        run[field] = int(run[field])
    return run


def record(data_dir, arguments):
    run = load_run(arguments)
    path, home = page(data_dir)
    today = datetime.date.today().isoformat()
    text = read(path)
    created = text is None
    if created:
        text = new_page(today, bool(home))
    marker = f"<!-- {PLUGIN}:{run['id']} -->"
    asked = run["questions"] > 0
    score = f"{run['correct']} / {run['misses']} / {run['disagreements']}" if asked else "not asked"
    row = (f"| {run['date']} | {cell(run['project'])} | {cell(run['target'])} | {run['questions'] if asked else '-'} | "
           f"{score} | {cell(run.get('pattern') or '-')} {marker} |")
    friction = cell(run.get("friction") or "")
    bullet = f"- {run['date']}, {cell(run['project'])}, {cell(run['target'])}: {friction} {marker}" if friction else None
    changed = put(put(text, RUNS, marker, row, HEADER), FRICTION, marker, bullet)
    if changed == text and not created:
        print(f"unchanged: run {run['id']} is already in {path}")
        return
    changed = re.sub(r"\A(---\n.*?^updated: )\S+", rf"\g<1>{today}", changed, count=1, flags=re.S | re.M)
    write(path, changed)
    if created and home:
        register(home, today)
    print(f"{'created' if created else 'updated'}: {path}")
    print(f"run {run['id']}: {score}" + (", friction noted" if bullet else ""))
    if home:
        print(f"vault: {home}" + (" (index.md and log.md got the new page)" if created else ""))
        print(commit(home, [path] + ([os.path.join(home, "index.md"), os.path.join(home, "log.md")] if created else []), run["id"]))


def where(data_dir, project_dir):
    path, home = page(data_dir)
    print(f"history: {path}" + ("" if os.path.exists(path) else " (not written yet)"))
    print(f"vault: {home}" if home else "vault: none, the history stays in the plugin's data directory")
    if project_dir:
        key = project_key(project_dir)
        backend = load_backends(data_dir).get(key)
        print(f"project: {key}")
        print(f"backend: {describe_backend(backend)}" if backend else "backend: none set, the walkthrough reads this project alone")


def runs(data_dir):
    path, _ = page(data_dir)
    text = read(path)
    found = section(text, RUNS) if text else None
    rows = [line for line in text[found[0]:found[1]].split("\n") if f"<!-- {PLUGIN}:" in line] if found else []
    if not rows:
        print(f"no runs recorded in {path}")
        return
    print(path)
    print(HEADER + "\n".join(rows))


def backend(data_dir, project_dir, value):
    key = project_key(project_dir)
    known = load_backends(data_dir)
    if value is None:
        print(f"project: {key}")
        print(f"backend: {describe_backend(known[key])}" if key in known else "backend: none set")
        return
    if value == "--clear":
        if known.pop(key, None) is None:
            print(f"nothing to clear for {key}")
            return
    else:
        target = os.path.realpath(absolute(value))
        if not os.path.isdir(target):
            fail(f"{target} is not a directory")
        if target == key:
            fail("the backend is the project itself; set it only when the other side lives in another checkout")
        known[key] = target
    write(os.path.join(data_dir, BACKENDS_FILE), json.dumps(known, indent=2, sort_keys=True) + "\n")
    print(f"project: {key}")
    print(f"backend: {describe_backend(known[key])}" if key in known else "backend: cleared")


def main():
    arguments = sys.argv[1:]
    if len(arguments) < 2 or arguments[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0 if arguments[:1] in (["-h"], ["--help"]) else 1)
    command, data_dir, rest = arguments[0], absolute(arguments[1]), arguments[2:]
    if command == "where" and len(rest) <= 1:
        where(data_dir, rest[0] if rest else None)
    elif command == "record" and rest:
        record(data_dir, rest)
    elif command == "runs" and not rest:
        runs(data_dir)
    elif command == "backend" and 1 <= len(rest) <= 2:
        backend(data_dir, rest[0], rest[1] if len(rest) == 2 else None)
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
