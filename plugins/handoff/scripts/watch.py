#!/usr/bin/env python3
"""flowkit-watch: a pipeline watched by a script, so no session waits for it.

  flowkit-watch add --story <slug> --mr <url> [--ready] [--until merged] [--left <text>]
  flowkit-watch add --story <slug> --ref <branch> [--sha <sha>] [--project <path>] [--name <n>]
  flowkit-watch add --story <slug> --check '<command>' --name <n>
  flowkit-watch list [--json]
  flowkit-watch drop <name>
  flowkit-watch poll [<name>]   one pass, as the poller makes every minute
  flowkit-watch run             the poller: one per machine, gone when no watch is open
  flowkit-watch ensure          starts the poller when a watch is open and none runs

`add` runs in the tree that was pushed and arms the sha of `@{u}`, not HEAD: a close commits its
docs after the push. It prints `watching <name> · …`; the name is unique per arm (`<repo dir>!<iid>`,
then `…#2`), so a section recorded for one arm never stands for the next. Kinds: `gitlab-mr` (the
MR's head pipeline, or the repo's `check = <command>` of capacity.conf), `gitlab-ref` (a branch's
pipeline at a sha) and `command` (exit 0 green, 1 red, 2 pending, anything else an error; its last
stdout line is the summary). A head that moved re-targets the arm; green, red and every answer that
ends a watch become a section in the story file through `handoff.py record`.

State: ${CLAUDE_CONFIG_DIR:-~/.claude}/flowkit/watches/<name>.json (one arm), <name>.lock, and
poller.pid, whose mtime is the poller's heartbeat. Events go to capacity's log.jsonl as `watch`.
"""
import argparse
import contextlib
import fcntl
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
POLL = 60  # seconds between two asks of one arm
ASK = 30  # seconds one ask may take
STALE = 3 * 60  # a heartbeat older than this no longer says a poller runs
NO_PIPELINE = 15 * 60  # a head with no pipeline for this long is reported
ERRORS = 15  # asks failed in a row (about 15 minutes) before the watch is reported broken
DEADLINE = 6 * 3600  # from the last arm
TAKEOVER = 10 * 60  # a close claimed this long ago is finished by whoever polls next
RUNNING = {"created", "waiting_for_resource", "preparing", "pending", "running", "scheduled"}
SETTLED = {"mergeable", "ci_still_running", "ci_must_pass", "checking", "unchecked", "preparing", "approvals_syncing"}
MR_URL = re.compile(r"^https?://[^/\s]+/(.+?)/-/merge_requests/(\d+)")
REMOTE = re.compile(r"^(?:https?://[^/]+/|(?:ssh://)?[^/@]+@[^:/]+[:/])(.+?)(?:\.git)?/?$")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._!#@+-]*$")
SCRUB = ("CLAUDE_PID", "TMUX_PANE")  # with CLAUDE_CODE_*: what would pass the poller off as a session


def _capacity():
    spec = importlib.util.spec_from_file_location("flowkit_capacity", HERE / "capacity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cap = _capacity()


class Broken(Exception):
    """An ask that got no answer: glab's auth, the network, a command that cannot run."""


def fail(msg):
    print(msg, file=sys.stderr)
    sys.exit(1)


def now():
    """The clock; FLOWKIT_WATCH_NOW (epoch seconds) stands in for it in tests."""
    return float(os.environ.get("FLOWKIT_WATCH_NOW") or time.time())


def stamp(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def clock(t):
    return time.strftime("%H:%M", time.localtime(t))


def watches():
    return cap.claude_dir() / "flowkit" / "watches"


def pidfile():
    return watches() / "poller.pid"


def story_file(slug):
    return cap.claude_dir() / "stories" / f"{slug}.md"


def sh(argv, cwd, timeout=ASK, env=None, stdin=None):
    """Exit code (None when it did not run or did not answer in time), stdout, the last stderr line."""
    try:
        proc = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                              **({"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}))
    except subprocess.TimeoutExpired:
        return None, "", f"no answer in {timeout:g} s"
    except OSError as e:
        return None, "", str(e)
    err = proc.stderr.strip().splitlines()
    return proc.returncode, proc.stdout, err[-1] if err else ""


def scrubbed():
    return {k: v for k, v in os.environ.items() if k not in SCRUB and not k.startswith("CLAUDE_CODE_")}


# ---- the arms ----

@contextlib.contextmanager
def locked(name):
    """<name>.lock, held only to read, compare and write: never across an ask."""
    watches().mkdir(parents=True, exist_ok=True)
    fd = os.open(watches() / f"{name}.lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def read(name):
    try:
        arm = json.loads((watches() / f"{name}.json").read_text())
    except (OSError, ValueError):
        return None
    return arm if isinstance(arm, dict) and arm.get("name") == name else None


def arms():
    try:
        names = sorted(e.name[:-5] for e in os.scandir(watches()) if e.name.endswith(".json") and e.name[0] != ".")
    except OSError:
        return []
    return [arm for arm in map(read, names) if arm]


def commit(arm, **changes):
    """Compare-on-write: under the arm's lock the changes land only while the file still holds the
    revision this caller read, so of two pollers, or a poll and an add, one wins. The arm as
    written, or None."""
    with locked(arm["name"]):
        have = read(arm["name"])
        if have is None or have.get("rev") != arm.get("rev"):
            return None
        new = {**have, **changes, "rev": have["rev"] + 1}
        cap.write_json(watches() / f"{arm['name']}.json", new)
        return new


def log(op, arm, t, **fields):
    cap.log("watch", t, op=op, name=arm["name"], story=arm["story"], **fields)


def note(arm, text, t):
    """An interim line in the story file: never a section, so it stands for no promise."""
    path = story_file(arm["story"])
    path.parent.mkdir(parents=True, exist_ok=True)
    head = "" if path.exists() else f"# {arm['story']}\n"
    with path.open("a") as f:
        f.write(f"{head}> watch {arm['name']} · {text} · {stamp(t)}\n")


# ---- add, list, drop ----

def git(tree, *args):
    code, out, _ = sh(["git", "-C", str(tree), *args], None)
    return out.strip() if code == 0 else ""


def unique(base, story):
    """base, then base#2, base#3: a name an open arm, a section of the story or the log already
    carries is never given again."""
    used = {arm["name"] for arm in arms()}
    with contextlib.suppress(OSError):
        used.update(cap.HEADING.findall(story_file(story).read_text(errors="replace")))
    used.update(ev.get("name") for ev in cap.events() if ev["event"] == "watch")
    n = 1
    while (name := base if n == 1 else f"{base}#{n}") in used:
        n += 1
    return name


def tmux_target():
    """The tmux server and session the watch was registered in: where its launches and doorbell go."""
    sock = os.environ.get("FLOWKIT_TMUX_SOCKET") or os.path.basename(os.environ.get("TMUX", "").split(",")[0])
    if not sock:
        return {"socket": "", "session": ""}
    pane = ["-t", os.environ["TMUX_PANE"]] if os.environ.get("TMUX_PANE") else []
    code, out, _ = sh(["tmux", "-L", sock, "display-message", "-p", *pane, "#{session_name}"], None, timeout=5)
    return {"socket": sock, "session": out.strip() if code == 0 else ""}


def add(args):
    t = now()
    story = re.sub(r"[^a-z0-9]+", "-", args.story.lower()).lstrip("-")[:32].rstrip("-")  # as `handoff.py record`
    if not story:
        fail(f"--story needs a slug: {args.story!r}")
    if args.ready and args.until == "merged":
        fail("--ready ends a watch at green; with --until merged the MR is out of Draft at the push")
    tree = cap.toplevel(args.tree or os.getcwd())
    repo_dir = re.sub(r"[^A-Za-z0-9._+-]", "-", os.path.basename(cap.main_checkout(tree)))
    upstream = git(tree, "rev-parse", "@{u}")
    check = None
    if args.mr:
        m = MR_URL.match(args.mr)
        if not m:
            fail(f"--mr takes a merge request's URL (…/-/merge_requests/<iid>), not {args.mr!r}")
        if not upstream:
            fail(f"nothing to watch: the branch in {tree} has no upstream (a watch arms the sha of @{{u}})")
        target = {"kind": "gitlab-mr", "project": m.group(1), "iid": int(m.group(2)), "url": m.group(0)}
        base, sha = args.name or f"{repo_dir}!{m.group(2)}", upstream
        check = (cap.repo_section(cap.load_config(), tree) or {}).get("check")
    elif args.ref:
        remote = REMOTE.match(git(tree, "remote", "get-url", "origin"))
        project = args.project or (remote.group(1) if remote else "")
        sha = args.sha or git(tree, "rev-parse", f"refs/remotes/origin/{args.ref}")
        if not project or not sha:
            fail(f"--ref needs a project and a sha: {tree} has no origin/{args.ref} to take them from "
                 "(--project, --sha)")
        target = {"kind": "gitlab-ref", "project": project, "ref": args.ref}
        base = args.name or f"{repo_dir}@{args.ref}"
    else:
        if not args.name:
            fail("--check needs --name: what the watch is called in the story file")
        target, base, sha = {"kind": "command", "command": args.check}, args.name, upstream
    if not NAME.match(base):
        fail(f"a watch's name is letters, digits and . _ ! # @ + -: {base!r}")
    glab = shutil.which("glab") if target["kind"] != "command" else None
    if target["kind"] != "command" and not glab:
        fail("glab is not on the PATH: a GitLab watch asks through it")
    me = {"sessionId": args.session, "name": ""} if args.session else cap.self_session(None)
    fresh = {**target, "base": base, "story": story, "sha": sha, "tree": tree, "check": check,
             "branch": git(tree, "rev-parse", "--abbrev-ref", "HEAD"), "target": "",
             "glab": os.path.abspath(glab) if glab else "", "until": args.until, "ready": args.ready,
             "by": {"session": me["sessionId"], "name": me["name"], "left": args.left or ""}, "tmux": tmux_target(),
             "state": "pending", "phase": "pipeline", "armed": t, "targeted": t, "polled": None, "answer": "",
             "errors": 0, "seen": None, "blocked": None, "noted": [], "retargets": 0, "did": []}
    arm = None
    for _ in range(5):  # an arm of this target still pending is armed again in place, under its own name
        same = next((a for a in arms() if a["state"] == "pending" and a["story"] == story and a["base"] == base
                     and all(a.get(k) == v for k, v in target.items())), None)
        if not same or (arm := commit(same, **fresh)):
            break
    if arm:
        log("arm", arm, t, sha=sha)
    while not arm:
        name = unique(base, story)
        with locked(name):
            if read(name) is None:
                arm = {"name": name, **fresh, "added": t, "rev": 1}
                cap.write_json(watches() / f"{name}.json", arm)
        if arm:
            log("add", arm, t, kind=arm["kind"], sha=sha, tree=tree, until=arm["until"])
    print(" · ".join(filter(None, [f"watching {arm['name']}", sha and f"sha {sha[:7]}", f"until {arm['until']}"])))
    if poller() is None:
        start()


def listing(as_json):
    found = arms()
    if as_json:
        print(json.dumps(found, indent=2))
        return
    for a in found:
        print(" · ".join(filter(None, [
            a["name"], a["story"], a["state"] + ("" if a["phase"] == "pipeline" else f" ({a['phase']})"),
            a["sha"] and f"sha {a['sha'][:7]}", f"armed {clock(a['armed'])}",
            a["polled"] and f"{clock(a['polled'])} {a['answer']}"])))
    if not found:
        print("no watch is open")


def drop(name, t):
    for _ in range(5):
        arm = read(name)
        if arm is None:
            fail(f"no watch named {name!r} (flowkit-watch list)")
        if arm["state"] == "closing":
            fail(f"{name} is closing: its section is being recorded")
        if close(arm, "dropped", "dropped", t):
            print(f"dropped {name}")
            return
    fail(f"{name} kept changing; try again")


# ---- the asks ----

def answer(kind, text, **more):
    """pending, green, red, report (an end that needs the user), retarget or merged."""
    return {"is": kind, "text": text, **more}


def glab_api(arm, path):
    code, out, err = sh([arm["glab"], "api", path], arm["tree"])
    if code != 0:
        raise Broken(err or (out.strip().splitlines() or [f"glab exited {code}"])[-1])
    try:
        return json.loads(out)
    except ValueError:
        raise Broken("glab api: the answer is not JSON") from None


def verdict(status, what, link):
    if status == "success":
        return answer("green", f"{what} green", link=link)
    if status == "failed":
        return answer("red", f"{what} red", link=link)
    if status in ("canceled", "skipped", "manual"):
        return answer("report", f"{what} {status}", link=link)
    return answer("pending", str(status))


def pipeline_of(arm, ref, sha, t):
    """A ref's newest pipeline at a sha."""
    rows = glab_api(arm, f"projects/{quote(arm['project'], safe='')}/pipelines?ref={quote(ref, safe='')}&sha={sha}")
    if not isinstance(rows, list):
        raise Broken("glab api: not a list of pipelines")
    if not rows:
        if t - arm["targeted"] > NO_PIPELINE:
            return answer("report", f"no pipeline for {sha[:7]} on {ref} after 15 min")
        return answer("pending", "no pipeline yet")
    return verdict(rows[0].get("status"), f"{ref} pipeline", rows[0].get("web_url") or "")


def command(arm, cmd):
    env = {**os.environ, "FLOWKIT_WATCH_NAME": arm["name"], "FLOWKIT_WATCH_SHA": arm["sha"],
           "FLOWKIT_WATCH_BRANCH": arm["branch"], "FLOWKIT_WATCH_MR": arm.get("url") or ""}
    code, out, err = sh(["sh", "-c", cmd], arm["tree"], env=env)
    last = (out.strip().splitlines() or [""])[-1]
    if code == 0:
        return answer("green", f"check green: {last}" if last else "check green")
    if code == 1:
        return answer("red", f"check red: {last}" if last else "check red")
    if code == 2:
        return answer("pending", last or "pending")
    raise Broken(err or last or f"the check exited {code}")


def merging(arm, mr, more):
    """A green MR that waits for its merge (--until merged): an approval keeps the watch polling, a
    conflict or a cancelled auto-merge ends it, once two polls in a row have seen it (the moment
    between green and the merge looks the same)."""
    status = mr.get("detailed_merge_status") or ""
    if mr.get("has_conflicts") or status in ("conflict", "need_rebase"):
        why = "merge blocked: conflict"
    elif mr.get("merge_when_pipeline_succeeds") is False:
        why = "auto-merge cancelled" + (" by a push" if arm["retargets"] else "")
    else:
        text = "" if status in SETTLED or not status else \
            "waiting for approval" if status == "not_approved" else f"waiting for the merge: {status.replace('_', ' ')}"
        return answer("pending", text or "waiting for the merge", note=text, **more)
    return answer("report" if arm["blocked"] == why else "pending", why, blocked=why, **more)


def merge_request(arm, t):
    mr = glab_api(arm, f"projects/{quote(arm['project'], safe='')}/merge_requests/{arm['iid']}")
    if not isinstance(mr, dict) or not mr.get("state"):
        raise Broken("glab api: not a merge request")
    more = {"target": mr.get("target_branch") or ""}
    if mr["state"] == "merged":
        return answer("merged", "MR merged", head=mr.get("sha") or arm["sha"], **more,
                      merged_sha=mr.get("merge_commit_sha") or mr.get("squash_commit_sha") or mr.get("sha") or arm["sha"])
    if mr["state"] != "opened":
        return answer("report", f"MR {mr['state']}", **more)
    if mr.get("sha") and mr["sha"] != arm["sha"]:  # someone pushed: the armed sha is no longer the head
        return answer("retarget", f"retarget {mr['sha'][:7]}", sha=mr["sha"], **more)
    if arm["phase"] == "merge":
        return merging(arm, mr, more)
    if arm["check"]:
        return {**command(arm, arm["check"]), **more}
    head = mr.get("head_pipeline")
    if not isinstance(head, dict) or not head.get("status"):
        if t - arm["targeted"] > NO_PIPELINE:
            return answer("report", f"no pipeline for {arm['sha'][:7]} after 15 min", **more)
        return answer("pending", "no pipeline yet", **more)
    found = verdict(head["status"], "pipeline", head.get("web_url") or "")
    seen = [head.get("id"), head["status"]]
    if found["is"] != "pending" and head.get("sha") != mr["sha"] and arm["seen"] != seen:
        # a merged-results pipeline carries another sha, so for the seconds after a push it cannot be
        # told from the last head's: its end counts at the second poll that sees it
        return answer("pending", f"{head['status']}, asked once more", seen=seen, **more)
    return {**found, **more}


def ask(arm, t):
    if arm["kind"] == "command":
        return command(arm, arm["command"])
    if arm["kind"] == "gitlab-ref":
        return pipeline_of(arm, arm["ref"], arm["sha"], t)
    if arm["phase"] != "target":
        return merge_request(arm, t)
    if not arm["target"]:
        raise Broken("the MR named no target branch")
    found = pipeline_of(arm, arm["target"], arm["merged_sha"], t)
    if found["is"] == "pending":
        return found
    # a red target is reported, never fixed: a fix there is a new MR
    return {**found, "is": "green" if found["is"] == "green" else "report", "text": f"merged; {found['text']}"}


# ---- one poll ----

def poll(name, t=None):
    t = now() if t is None else t
    with locked(name):
        arm = read(name)
    if arm is None:
        return
    if arm["state"] == "closing":  # a close its poller did not finish
        if arm["closer"] == os.getpid() or not cap.pid_alive(arm["closer"]) or time.time() - arm["claimed"] > TAKEOVER:
            mine = commit(arm, closer=os.getpid(), claimed=time.time())
            if mine:
                finish(mine, t)
        return
    if arm["state"] != "pending":
        return
    try:
        found = ask(arm, t)
    except Exception as e:  # an answer in a shape nobody expected is an ask that failed, too
        n = arm["errors"] + 1
        err = str(e) or type(e).__name__
        new = commit(arm, errors=n, polled=t, answer=f"error: {err}")
        if new and n == 1:
            log("error", new, t, error=err)
        if new and n >= ERRORS:
            close(new, "report", f"watch broken: {err}", t)
        return
    settle(arm, found, t)


def settle(arm, found, t):
    keep = {"polled": t, "answer": found["text"], "errors": 0, "seen": found.get("seen"), "blocked": found.get("blocked"),
            "target": found.get("target") or arm["target"]}
    kind = found["is"]
    if kind == "retarget":
        new = commit(arm, **keep, sha=found["sha"], targeted=t, phase="pipeline", retargets=arm["retargets"] + 1)
        if new:
            log("retarget", new, t, sha=found["sha"])
            note(new, found["text"], t)
    elif kind == "pending":
        text = found.get("note") if found.get("note") not in arm["noted"] else ""
        new = commit(arm, **keep, noted=arm["noted"] + [text] if text else arm["noted"])
        if new and text:
            note(new, text, t)
    elif kind == "green" and arm["until"] == "merged" and arm["phase"] == "pipeline":
        new = commit(arm, **keep, phase="merge")
        if new:
            log("green", new, t, sha=new["sha"])
    elif kind == "merged" and arm["until"] == "merged":
        new = commit(arm, **keep, phase="target", merged_sha=found["merged_sha"], targeted=t)
        if new:
            log("merged", new, t, sha=found["merged_sha"])
            back, why = back_to_default(new, found["head"])
            if why:
                note(new, f"merged; {new['tree']} left on {new['branch']}: {why}", t)
            new = commit(new, back=back) or new
    else:
        if kind == "merged":  # merged by hand before the watch saw green: nothing left to ready
            keep["ready"] = False
        close(arm, "green" if kind in ("green", "merged") else kind, found["text"], t, link=found.get("link") or "", **keep)
        return
    if new and t - new["armed"] > DEADLINE:
        close(new, "report", f"no result in 6 h (last answer: {found['text']})", t, op="deadline")


def close(arm, kind, what, t, op=None, link="", **keep):
    """An arm's end (green, red, report or dropped), claimed under its lock; the claimer alone
    finishes it. False when another poll or an add changed the arm first."""
    new = commit(arm, **keep, state="closing", closer=os.getpid(), claimed=time.time(),
                 outcome={"kind": kind, "what": what, "at": t, "link": link})
    if not new:
        return False
    if kind != "dropped" and (kind != "green" or new["phase"] != "target"):  # a merged arm's green is in the log
        log(op or kind, new, t, what=what, sha=new["sha"])
    finish(new, t)
    return True


def finish(arm, t):
    """What a claimed arm still owes: the MR out of Draft (--ready), its section through `handoff.py
    record`, then its file goes. A step done is written down first, so a poller that takes over a
    dead one's close repeats nothing."""
    out = arm["outcome"]
    if out["kind"] == "green" and arm["ready"] and "ready" not in arm["did"]:
        code, _, err = sh([arm["glab"], "mr", "update", str(arm["iid"]), "--ready"], arm["tree"], timeout=60)
        arm = commit(arm, did=arm["did"] + ["ready"], ready_error="" if code == 0 else err or f"glab exited {code}")
        if arm is None:
            return
    if not recorded(arm):
        text = section(arm, t)
        env = {**os.environ, **({"FLOWKIT_TMUX_SOCKET": arm["tmux"]["socket"]} if arm["tmux"]["socket"] else {})}
        code, _, _ = sh([sys.executable, str(HERE / "handoff.py"), "record", arm["story"]], None, timeout=300,
                        env=env, stdin=text + "\n")
        if code != 0 and not recorded(arm):  # the doorbell and the drain are record's; the section is owed
            with story_file(arm["story"]).open("a") as f:
                f.write(f"\n{text}\n")
    with locked(arm["name"]):
        for suffix in ("json", "lock"):
            (watches() / f"{arm['name']}.{suffix}").unlink(missing_ok=True)
    log("done", arm, t, outcome=out["what"], minutes=round((out["at"] - arm["armed"]) / 60))


def recorded(arm):
    try:
        return bool(re.search(rf"^## {re.escape(arm['name'])} · ", story_file(arm["story"]).read_text(errors="replace"), re.M))
    except OSError:
        return False


def left_of(arm):
    """The LEFT of the close that registered the watch, from the story file, when `add` got none."""
    try:
        text = story_file(arm["story"]).read_text(errors="replace")
    except OSError:
        return ""
    for part in re.split(r"^(?=## )", text, flags=re.M):
        if arm["by"]["session"] and f"Session: {arm['by']['session']}" in part:
            m = re.search(r"^LEFT\b[ \t]*-?[ \t]*(.*?)(?=^> |\Z)", part, re.M | re.S)
            return m.group(1).strip() if m else ""
    return ""


def section(arm, t):
    out = arm["outcome"]
    done, for_me, still = "nothing new", "nothing new", "nothing"
    if out["kind"] == "green":
        done = out["what"]
        if "ready" in arm["did"] and arm["ready_error"]:
            done += "; MR still Draft"
            for_me = f"1. take the MR out of Draft, the watch could not ({arm['ready_error']}): {arm['url']}"
        elif "ready" in arm["did"]:
            done += "; MR ready"
        if arm.get("back"):
            done += f"; {arm['back']}"
    elif out["kind"] == "dropped":
        still = f"no longer watched: {arm.get('url') or arm['name']}"
    else:
        for_me = f"1. {out['what']}: {out['link'] or arm.get('url') or arm['name']}"
        still = out["what"]
    left = (arm["by"]["left"] or left_of(arm) or "see the close that registered this watch").strip()
    left = f"LEFT\n{left}" if "\n" in left else f"LEFT - {left.removeprefix('- ')}"
    watch = " · ".join(filter(None, [f"Watch: {out['what']} {clock(out['at'])}", arm["sha"] and f"sha {arm['sha'][:7]}",
                                     f"{round((out['at'] - arm['armed']) / 60)} min", arm.get("url")]))
    return "\n".join([f"## {arm['name']} · {stamp(t)}", watch, "Next: none", f"DONE - {done}", f"FOR ME - {for_me}",
                      f"OPEN - {still}", left])


# ---- after the merge ----

def working_in(tree):
    """Who works in the tree: a live session there, a reporter and a slice whose close is recorded
    (unless busy again) aside. Sessions unknown counts as somebody."""
    rows = cap.snapshot()
    if rows is None:
        return "the sessions are unknown (`claude agents` did not answer)"
    _, closed = cap.stories()
    for row in rows:
        name = row.get("name") or ""
        if not row.get("pid") or not row.get("cwd") or name.endswith("-report"):
            continue
        if row["sessionId"] in closed and row.get("status") != "busy":
            continue
        if cap.toplevel(row["cwd"]) == os.path.realpath(tree):
            return f"session {name or row['sessionId'][:8]} works there"
    return ""


def back_to_default(arm, head):
    """The mechanical back-to-default once the MR merged: only in a main checkout still on the MR's
    branch, clean, with nobody working in it. Switches to the default branch, pulls fast-forward
    only and deletes the branch when the merged head holds all of it. Returns what was done for the
    section's DONE, and why nothing was (an interim line)."""
    tree, branch = arm["tree"], arm["branch"]

    def run(*args, timeout=ASK):
        return sh(["git", "-C", tree, *args], None, timeout=timeout)

    if not os.path.isdir(tree):
        return "", "the tree is gone"
    if cap.main_checkout(tree) != os.path.realpath(tree):
        return "", "a worktree, not the main checkout"
    if run("rev-parse", "--abbrev-ref", "HEAD")[1].strip() != branch:
        return "", "another branch is checked out"
    if run("status", "--porcelain", "--untracked-files=no")[1].strip():
        return "", "uncommitted changes"
    who = working_in(tree)
    if who:
        return "", who
    default = run("symbolic-ref", "--short", "refs/remotes/origin/HEAD")[1].strip().partition("/")[2] or arm["target"]
    if not default or default == branch:
        return "", "no default branch to switch to"
    code, _, err = run("switch", default)
    if code != 0:
        return "", f"git switch {default}: {err}"
    code, _, err = run("pull", "--ff-only", timeout=120)
    if code != 0:
        return f"on {default}, not pulled ({err}), {branch} kept", ""
    if run("merge-base", "--is-ancestor", branch, head)[0] != 0:
        return f"back on {default}, {branch} kept (it has commits the merge did not take)", ""
    code, _, err = run("branch", "-D", branch)
    return f"back on {default}, {branch} " + ("deleted" if code == 0 else f"kept ({err})"), ""


# ---- the poller ----

def poller():
    """The pid in poller.pid while a poller holds it: alive, and either its heartbeat is fresh or
    the process still is this script (a pid can be given to something else)."""
    try:
        pid = int(pidfile().read_text())
        fresh = time.time() - pidfile().stat().st_mtime < STALE
    except (OSError, ValueError):
        return None
    if not cap.pid_alive(pid):
        return None
    return pid if fresh or "watch.py" in sh(["ps", "-o", "command=", "-p", str(pid)], None)[1] else None


def take():
    """poller.pid, created with O_EXCL; one a dead poller left is replaced. False while another
    poller holds it."""
    with locked(".poller"):
        if poller() not in (None, os.getpid()):
            return False
        pidfile().unlink(missing_ok=True)
        fd = os.open(pidfile(), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True


def start():
    subprocess.Popen([sys.executable, str(HERE / "watch.py"), "run"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env=scrubbed(),
                     cwd=str(Path.home()))


def ensure():
    """The tick, every `handoff.py record` and every flowkit-spawn end call this: a watch that is
    open with no poller gets one."""
    if arms() and poller() is None:
        start()


def run():
    """The one poller: asks every open arm once a minute, keeps the machine awake meanwhile, touches
    its heartbeat every loop and is gone when no arm is open."""
    for key in set(os.environ) - set(scrubbed()):
        del os.environ[key]
    if not take():
        print(f"a poller runs already (pid {poller()})")
        return
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    awake = None
    if shutil.which("caffeinate"):
        awake = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    every = float(os.environ.get("FLOWKIT_WATCH_POLL") or POLL)
    asked = {}
    try:
        while True:
            try:
                os.utime(pidfile())
            except OSError:
                if not take():
                    return
            names = [arm["name"] for arm in arms()]
            if not names:
                pidfile().unlink(missing_ok=True)
                if not arms() or not take():  # an add can land as the last watch goes
                    return
                continue
            for name in names:
                if time.monotonic() - asked.get(name, -every) >= every:
                    asked[name] = time.monotonic()
                    try:
                        poll(name)
                    except Exception as e:
                        print(f"{name}: {type(e).__name__}: {e}", file=sys.stderr)
                    with contextlib.suppress(OSError):
                        os.utime(pidfile())
            time.sleep(min(every, 5))
    finally:
        if awake:
            awake.terminate()
        with locked(".poller"):
            with contextlib.suppress(OSError, ValueError):
                if int(pidfile().read_text()) == os.getpid():
                    pidfile().unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="flowkit-watch", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--story", required=True)
    what = a.add_mutually_exclusive_group(required=True)
    what.add_argument("--mr")
    what.add_argument("--ref")
    what.add_argument("--check")
    for flag in ("--name", "--sha", "--project", "--tree", "--left", "--session"):
        a.add_argument(flag)
    a.add_argument("--ready", action="store_true")
    a.add_argument("--until", choices=("green", "merged"), default="green")
    sub.add_parser("list").add_argument("--json", action="store_true")
    sub.add_parser("drop").add_argument("name")
    sub.add_parser("poll").add_argument("name", nargs="?")
    sub.add_parser("run")
    sub.add_parser("ensure")
    args = parser.parse_args(argv)
    if args.cmd == "add":
        add(args)
    elif args.cmd == "list":
        listing(args.json)
    elif args.cmd == "drop":
        drop(args.name, now())
    elif args.cmd == "poll":
        for name in [args.name] if args.name else [arm["name"] for arm in arms()]:
            poll(name)
    elif args.cmd == "run":
        run()
    else:
        ensure()


if __name__ == "__main__":
    main()
