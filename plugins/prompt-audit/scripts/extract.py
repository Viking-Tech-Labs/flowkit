#!/usr/bin/env python3
"""Pull your own prompts out of the Claude Code transcripts on this machine.

  extract.py run <data-dir> <out-dir> [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--transcripts DIR]
                                    read every transcript, write the corpus into <out-dir> and
                                    print a summary; changes nothing in <data-dir>
  extract.py record <data-dir> <out-dir> [report.md]
                                    note the window of the run in <out-dir> as audited, and keep
                                    a copy of the report, so the next run starts where it ended;
                                    run again with a report, it replaces that window's report
  extract.py runs <data-dir>        list the audits recorded so far

Transcripts are read from --transcripts, else from projects/ in $CLAUDE_CONFIG_DIR, else from
~/.claude/projects. --since defaults to the end of the last recorded audit; with none recorded
the whole history is read. <data-dir> keeps runs.json, reports/ and, when you write one,
launch-patterns.txt: one regular expression per line for machine-drafted prompts that reach a
session looking typed (a prompt another session or a script wrote for you).

<out-dir> receives prompts.jsonl (one line per user turn, with a `kind`), one <project>.txt per
project (typed prompts only), projects.tsv (project, typed prompts, working directory) and
window.json; the files the previous run wrote there are removed first. A session resumed under a
new id (its transcript ends with a continued-in marker, the new one holds a copy of its history)
counts once, under the last id of the chain. These files hold your prompts verbatim: <out-dir>
must be a scratch directory, and the script refuses one inside a git repository. The summary
never prints a secret: the scan reports where one sits, not its value.

Kinds:
  typed     typed, dictated, pasted or queued by you; the corpus
  accepted  a prompt suggestion you accepted, kept apart
  command   a slash command you ran
  launch    a machine-drafted prompt: the long first prompt of a session that was named before
            its first turn, or one that matches a line of launch-patterns.txt

Exit codes: 0 done, 1 usage, unreadable input or a refused <out-dir>.
"""
import argparse
import collections
import datetime
import glob
import json
import os
import re
import shutil
import statistics
import sys

SECRET = re.compile(r"whsec_[A-Za-z0-9]{8,}|ATATT[A-Za-z0-9_\-]{20,}|glpat-[A-Za-z0-9_\-]{12,}|"
                    r"xox[bpa]-[A-Za-z0-9\-]{10,}|ghp_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|"
                    r"sk-(?:ant-|live_|proj-)?[A-Za-z0-9_\-]{20,}|sk_live_[A-Za-z0-9]{16,}|re_[A-Za-z0-9]{20,}")
RUNS_FILE = "runs.json"
PATTERNS_FILE = "launch-patterns.txt"
WINDOW_FILE = "window.json"


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def absolute(path):
    return os.path.abspath(os.path.expanduser(path))


def transcripts_dir(argument):
    if argument:
        return absolute(argument)
    config = os.environ.get("CLAUDE_CONFIG_DIR", "").strip() or "~/.claude"
    return os.path.join(absolute(config), "projects")


def in_repository(path):
    while True:
        if os.path.exists(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def load_runs(data_dir):
    try:
        with open(os.path.join(data_dir, RUNS_FILE), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return []


def launch_patterns(data_dir):
    try:
        with open(os.path.join(data_dir, PATTERNS_FILE), encoding="utf-8") as handle:
            lines = [line.strip() for line in handle if line.strip() and not line.startswith("#")]
    except OSError:
        return None
    try:
        return re.compile("|".join(f"(?:{line})" for line in lines), re.M) if lines else None
    except re.error as error:
        fail(f"{os.path.join(data_dir, PATTERNS_FILE)} holds an invalid regular expression: {error}")


def vault(data_dir):
    """The vault of the brain plugin when one resolves; the report can be offered as a page there."""
    candidates = [os.environ.get("BRAIN_DIR", "").strip()]
    name = os.path.basename(os.path.normpath(data_dir))
    if name.startswith("prompt-audit"):
        path_file = os.path.join(os.path.dirname(os.path.normpath(data_dir)), "brain" + name[len("prompt-audit"):], "vault-path")
        try:
            with open(path_file, encoding="utf-8") as handle:
                candidates.append(handle.read().strip())
        except OSError:
            pass
    for candidate in candidates:
        if candidate and os.path.isdir(os.path.join(absolute(candidate), "wiki")) and os.path.exists(os.path.join(absolute(candidate), "index.md")):
            return absolute(candidate)
    return None


def project(slug):
    home = re.sub(r"[^A-Za-z0-9]", "-", os.path.expanduser("~"))
    if slug == home:
        return "home"
    return slug[len(home) + 1:] if slug.startswith(home + "-") else slug.lstrip("-")


def text_of(d):
    c = d["message"].get("content")
    if isinstance(c, list):
        if any(b.get("type") == "tool_result" for b in c):
            return None
        c = "\n".join(b.get("text", "") for b in c if b.get("type") == "text")
    return re.sub(r"<system-reminder>.*?</system-reminder>", "", c or "", flags=re.S).strip()


def in_window(ts, since, until):
    if since and (ts[:10] < since if len(since) == 10 else ts[:19] <= since):
        return False
    return ts[:10] <= until


def run(a):
    out = absolute(a.out)
    repository = in_repository(out)
    if repository:
        fail(f"{out} is inside the git repository {repository}. The extract holds your prompts verbatim: "
             "give a scratch directory outside any repository.")
    source = transcripts_dir(a.transcripts)
    if not os.path.isdir(source):
        fail(f"No transcripts directory at {source}. Pass --transcripts DIR.")
    runs = load_runs(a.data)
    since = a.since or (runs[-1]["until"] if runs else None)
    launch = launch_patterns(a.data)
    os.makedirs(out, exist_ok=True)
    try:  # drop what the previous run left here, so a project that went quiet leaves no stale file
        with open(os.path.join(out, WINDOW_FILE), encoding="utf-8") as handle:
            stale = json.load(handle).get("files", [])
    except (OSError, ValueError):
        stale = []
    for name in stale:
        if os.path.basename(name) == name and os.path.isfile(os.path.join(out, name)):
            os.remove(os.path.join(out, name))

    rows, secrets, cwds, markers, transcripts = [], [], {}, {}, set()
    for f in sorted(glob.glob(os.path.join(source, "*", "*.jsonl"))):
        proj = project(os.path.basename(os.path.dirname(f)))
        own = os.path.splitext(os.path.basename(f))[0]
        transcripts.add(own)
        named, first = False, True
        for line in open(f, encoding="utf-8", errors="replace"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            if d.get("type") == "agent-name":
                named = True
            elif d.get("type") == "continued-in" and d.get("continuedInSessionId"):
                markers[d.get("sessionId") or own] = d["continuedInSessionId"]
            if d.get("type") != "user" or d.get("isMeta") or d.get("isSidechain") or d.get("isCompactSummary"):
                continue
            if d.get("entrypoint") == "sdk-cli" or d.get("promptSource") == "sdk":
                continue  # `claude -p` pipelines, not a person
            t = text_of(d)
            if not t or t.startswith(("<system-reminder>", "<task-notification", "<local-command", "[Request interrupted")):
                continue
            ts = d.get("timestamp", "")
            human = not t.startswith(("<command-", "/"))
            if not in_window(ts, since, a.until):
                first = first and not human
                continue
            if not human:
                m = re.search(r"<command-name>(.*?)</command-name>.*?<command-args>(.*?)</command-args>", t, re.S)
                kind, t = "command", (f"{m.group(1)} {m.group(2)}".strip() if m else t[:200])
            elif (launch and launch.search(t)) or (first and named and len(t) > 1500):
                kind = "launch"
            elif d.get("promptSource") == "suggestion_accepted":
                kind = "accepted"
            else:
                kind = "typed"
            first = first and not human
            rows.append({"ts": ts[:19], "project": proj, "session": d.get("sessionId", ""), "kind": kind,
                         "branch": d.get("gitBranch", ""), "chars": len(t), "text": t,
                         "uuid": d.get("uuid") or f"{ts} {t}", "cwd": d.get("cwd")})

    # A resumed session gets a new id and a copy of its history, and the original's transcript
    # ends with a continued-in marker: its prompts belong to the last id of the chain whose
    # transcript exists. Originals go before their continuations, so the original's row wins and
    # a second row with the same uuid is a continuation's or a fork's copy. The (session, text)
    # rule then still drops a prompt repeated in one session (a doorbell, an API-error continue).
    def final(sid):
        visited = set()
        while sid in markers and markers[sid] in transcripts and sid not in visited:
            visited.add(sid)
            sid = markers[sid]
        return sid

    origins = {new: old for old, new in markers.items() if new in transcripts}

    def depth(sid):
        n, visited = 0, set()
        while sid in origins and sid not in visited:
            visited.add(sid)
            sid, n = origins[sid], n + 1
        return n

    rows.sort(key=lambda r: depth(r["session"]))
    kept, continued, merged, forks, seen, unique = {}, set(), 0, 0, set(), []
    for r in rows:
        sid = final(r["session"])
        if sid != r["session"]:
            continued.add(r["session"])
        if r["uuid"] in kept:
            if kept[r["uuid"]] == sid:
                merged += 1
            else:
                forks += 1
            continue
        kept[r["uuid"]] = sid
        r["session"] = sid
        if (sid, r["text"]) in seen:
            continue
        seen.add((sid, r["text"]))
        if SECRET.search(r["text"]):
            secrets.append((r["ts"][:16], r["project"], sid, r["kind"]))
        if r["cwd"]:
            cwds.setdefault(r["project"], r["cwd"])
        del r["uuid"], r["cwd"]
        unique.append(r)
    rows = unique

    rows.sort(key=lambda r: r["ts"])
    with open(os.path.join(out, "prompts.jsonl"), "w", encoding="utf-8") as handle:
        for r in rows:
            handle.write(json.dumps(r, ensure_ascii=False) + "\n")
    typed = [r for r in rows if r["kind"] == "typed"]
    byproj = collections.defaultdict(list)
    for r in typed:
        byproj[r["project"]].append(r)
    ranked = sorted(byproj.items(), key=lambda x: -len(x[1]))
    for p, rs in ranked:
        with open(os.path.join(out, f"{p}.txt"), "w", encoding="utf-8") as handle:
            for r in rs:
                handle.write(f"[{r['ts'][:16]} {r['session'][:8]} {r['branch']}] {r['text']}\n\n")
    with open(os.path.join(out, "projects.tsv"), "w", encoding="utf-8") as handle:
        for p, rs in ranked:
            handle.write(f"{p}\t{len(rs)}\t{cwds.get(p, '')}\n")
    window = {"since": since, "until": rows[-1]["ts"] if rows else since,
              "typed": len(typed), "sessions": len({r["session"] for r in typed}), "transcripts": source,
              "files": ["prompts.jsonl", "projects.tsv"] + [f"{p}.txt" for p, _ in ranked]}
    with open(os.path.join(out, WINDOW_FILE), "w", encoding="utf-8") as handle:
        json.dump(window, handle, indent=2)
        handle.write("\n")

    origin = "given" if a.since else "end of the last recorded audit" if runs else "first audit"
    print(f"window {since or 'the whole history'} ({origin}) → {window['until'] or 'nothing found'}")
    print(f"transcripts {source}")
    print(f"out {out}")
    kinds = collections.Counter(r["kind"] for r in rows)
    print("kinds:", dict(kinds.most_common()))
    print(f"typed: {len(typed)} prompts, {window['sessions']} sessions, "
          f"median {int(statistics.median([r['chars'] for r in typed])) if typed else 0} chars, "
          f"{sum(r['chars'] < 120 for r in typed)} under 120")
    print(f"continued sessions: {len(continued)} ({merged} prompts merged), fork copies dropped: {forks}")
    print("typed per project:", ", ".join(f"{p} {len(rs)}" for p, rs in ranked))
    cmds = collections.Counter(r["text"].split()[0] for r in rows if r["kind"] == "command")
    print("commands:", ", ".join(f"{c} {n}" for c, n in cmds.most_common(15)))
    print("accepted suggestions:", len([r for r in rows if r["kind"] == "accepted"]))
    print(f"possible secrets: {len(secrets)}" + "".join(f"\n  {s[0]} {s[1]} {s[2]} ({s[3]})" for s in secrets))
    report = runs[-1].get("report") if runs else None
    print("last report:", report if report and os.path.exists(report) else "none")
    print("vault:", vault(a.data) or "none")


def record(a):
    try:
        with open(os.path.join(absolute(a.out), WINDOW_FILE), encoding="utf-8") as handle:
            window = json.load(handle)
    except (OSError, ValueError):
        fail(f"No {WINDOW_FILE} in {absolute(a.out)}: run the extract first.")
    if not window["until"]:
        fail("The run found no prompts, so there is no window to record.")
    runs = load_runs(a.data)
    again = bool(runs) and runs[-1]["until"] == window["until"]
    if again and not a.report:
        print(f"The window ending {window['until']} is already recorded. Nothing written.")
        return
    entry = runs.pop() if again else {
        "date": datetime.date.today().isoformat(), "since": window["since"], "until": window["until"],
        "typed": window["typed"], "sessions": window["sessions"], "report": None}
    os.makedirs(a.data, exist_ok=True)
    if a.report:
        if not os.path.isfile(absolute(a.report)):
            fail(f"No report at {absolute(a.report)}.")
        os.makedirs(os.path.join(a.data, "reports"), exist_ok=True)
        name = "audit-" + re.sub(r"[^0-9T-]", "", entry["until"]) + ".md"
        entry["report"] = os.path.join(absolute(a.data), "reports", name)
        shutil.copyfile(absolute(a.report), entry["report"])
    with open(os.path.join(a.data, RUNS_FILE), "w", encoding="utf-8") as handle:
        json.dump(runs + [entry], handle, indent=2)
        handle.write("\n")
    if again:
        print(f"The window ending {entry['until']} was already recorded; its report was replaced.")
    else:
        print(f"Recorded the window {entry['since'] or 'the whole history'} → {entry['until']} "
              f"({entry['typed']} typed prompts). The next audit starts after it.")
    if entry["report"]:
        print(f"Report kept at {entry['report']}")


def show(a):
    runs = load_runs(a.data)
    if not runs:
        print("No audit recorded yet.")
    for r in runs:
        print(f"{r['date']}  {r['since'] or 'the whole history'} → {r['until']}  "
              f"{r['typed']} typed prompts  {r.get('report') or 'no report kept'}")


def main():
    ap = argparse.ArgumentParser(usage=__doc__)
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run")
    p.add_argument("data")
    p.add_argument("out")
    p.add_argument("--since")
    p.add_argument("--until", default="9999")
    p.add_argument("--transcripts")
    p.set_defaults(action=run)
    p = sub.add_parser("record")
    p.add_argument("data")
    p.add_argument("out")
    p.add_argument("report", nargs="?")
    p.set_defaults(action=record)
    p = sub.add_parser("runs")
    p.add_argument("data")
    p.set_defaults(action=show)
    a = ap.parse_args()
    a.action(a)


if __name__ == "__main__":
    main()
