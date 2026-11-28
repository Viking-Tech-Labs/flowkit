#!/usr/bin/env python3
"""flowkit-watch: a pipeline watched by a script, so no session waits for it.

  flowkit-watch add --story <slug> --mr <url> [--ready] [--until merged] [--left <text>]
  flowkit-watch add --story <slug> --ref <branch> [--sha <sha>] [--project <path>] [--name <n>]
  flowkit-watch add --story <slug> --check '<command>' --name <n>
      any of them: [--on-green-run '<command>'] [--on-green <prompt> [--repo <tree>] [--on-green-name <n>]]
  flowkit-watch list [--json]
  flowkit-watch drop <name>
  flowkit-watch poll [<name>]   one pass, as the poller makes every minute
  flowkit-watch run             the poller: one per machine, gone when no watch is open
  flowkit-watch ensure          starts the poller when a watch is open and none runs
  flowkit-watch launch <name>   the detached wrapper of a launch the watch makes

`add` runs in the tree that was pushed and arms the sha of `@{u}`, not HEAD: a close commits its
docs after the push. It prints `watching <name> · …`; the name is unique per arm (`<repo dir>!<iid>`,
then `…#2`), so a section recorded for one arm never stands for the next. Kinds: `gitlab-mr` (the
MR's head pipeline, or the repo's `check = <command>` of capacity.conf), `gitlab-ref` (a branch's
pipeline at a sha) and `command` (exit 0 green, 1 red, 2 pending, anything else an error; its last
stdout line is the summary). `--mr` (or `--pr`) takes a GitHub pull request's URL too: `github-pr`
is the same watch asked through `gh` (the checks of the head as GitHub rolls them up), and a ref of
a github.com repository is a `github-ref`. A head that moved re-targets the arm; green, red and
every answer that ends a watch become a section in the story file through `handoff.py record`.

An MR's first red starts one fix slice (fix-brief.md, through `flowkit-spawn --from-watch`): in the
registered tree when it is free and still on the MR's branch, in a repository of one checkout
(`checkouts = 1` in capacity.conf) queued for that checkout, else in a detached worktree under
`.claude/worktrees/`, which goes with the arm. The arm stays `fixing` until the fix slice's close
arms the next name; a second red is reported. An MR whose target is the branch of another arm that
is red waits for it (`waiting-base`). On green `--on-green-run` runs a command in the tree and
`--on-green` starts a prompt (in --repo, as --on-green-name, default the prompt file's name).

State: ${CLAUDE_CONFIG_DIR:-~/.claude}/flowkit/watches/<name>.json (one arm), <name>.lock, and
poller.pid, whose mtime is the poller's heartbeat (poller.kinds: the kinds of arm that poller
asks); launched/<session>.log is the output of a launch
the watch made. Events go to capacity's log.jsonl as `watch`; capacity's status.json lists the open
arms (`watches`), rewritten whenever one opens, changes state or goes.
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
import statistics
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
STARTING = 60  # a launch wrapper that has not claimed its arm this long after its start is started again
BEAT = 30  # seconds between two beats of a wrapper's claim while flowkit-spawn runs (a load hold can take long)
GONE = 10 * 60  # a fix slice not listed, queued or closed this long after its launch is gone
RUN = 5 * 60  # seconds an --on-green-run command may take
CAPPED = {"max": "xhigh"}  # the effort of a launch nobody approved
SHELLS = {"zsh", "bash", "fish", "sh", "dash", "ksh", "tcsh", "csh"}  # a bare shell in a tree is nobody working
EXITS = {2: "bad arguments", 3: "no tmux to launch in", 4: "a session of that name runs", 5: "the pane is busy",
         6: "the tree has uncommitted changes", 7: "the launch driver is not available",
         8: "the folder is not trusted yet", 9: "`claude --bg` failed", 10: "claude exited twice at start-up"}
SESSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")  # a session name flowkit-spawn takes
RUNNING = {"created", "waiting_for_resource", "preparing", "pending", "running", "scheduled"}
SETTLED = {"mergeable", "ci_still_running", "ci_must_pass", "checking", "unchecked", "preparing", "approvals_syncing"}
MR_URL = re.compile(r"^https?://[^/\s]+/(.+?)/-/merge_requests/(\d+)")
PR_URL = re.compile(r"^https?://([^/\s]+)/([^/\s]+/[^/\s]+)/pull/(\d+)")
REMOTE = re.compile(r"^(?:https?://[^/]+/|(?:ssh://)?[^/@]+@[^:/]+[:/])(.+?)(?:\.git)?/?$")
HOST = re.compile(r"^(?:[a-z+]+://)?(?:[^/@]+@)?([^:/]+)")  # a remote URL's host
KINDS = ("gitlab-mr", "gitlab-ref", "command", "github-pr", "github-ref")  # a poller of 0.17.0 or older asks the first three
REQUESTS = ("gitlab-mr", "github-pr")  # a merge request or a pull request: what a red starts a fix slice for
FAILED = {"FAILURE", "TIMED_OUT", "STARTUP_FAILURE", "ERROR"}  # a GitHub check that a fix slice can answer
STOPPED = ("CANCELLED", "ACTION_REQUIRED", "STALE")  # one that only its user can
# one ask for a GitHub arm: a commit's checks as GitHub rolls them up and, for a pull request, its state
QUERY = """query($owner:String!,$name:String!,$oid:GitObjectID!%s){repository(owner:$owner,name:$name){%s
object(oid:$oid){...on Commit{statusCheckRollup{state contexts(first:100){nodes{__typename
...on CheckRun{name conclusion startedAt detailsUrl checkSuite{workflowRun{workflow{name}}}}
...on StatusContext{context state targetUrl createdAt}}}}}}}}"""
PULL = ("pullRequest(number:$number){state headRefOid baseRefName mergeable mergeStateStatus reviewDecision "
        "autoMergeRequest{enabledAt} mergeCommit{oid}}")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._!#@+-]*$")
SCRUB = ("CLAUDE_PID", "TMUX_PANE", "FLOWKIT_TARGET")  # with CLAUDE_CODE_*: what would pass the poller off as a session, and where another arm's ring went
SHOWN = ("state", "phase", "sha", "fix", "waits")  # what capacity's status.json says of an open arm, besides its name


def _capacity():
    spec = importlib.util.spec_from_file_location("flowkit_capacity", HERE / "capacity.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cap = _capacity()


class Broken(Exception):
    """An ask that got no answer: glab's auth, the network, a command that cannot run."""


class Stuck(Exception):
    """No tree for a slice the watch would start: a report, never a guess."""


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
    if any(new.get(k) != have.get(k) for k in SHOWN):
        cap.write_watches(now())
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


def where():
    """Where the watch was registered, in the word of the backend that has one for it (backend.py's
    target()): where its launches and its doorbell go. "" when none has."""
    spec = importlib.util.spec_from_file_location("flowkit_backend", HERE / "backend.py")
    backend = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(backend)
        for at in backend.BACKENDS.values():
            with contextlib.suppress(backend.Refused, OSError, subprocess.SubprocessError):
                word = at.target()
                if word:
                    return word
    except Exception:
        pass
    return ""


def reach(arm):
    """How the arm's doorbell and its launches reach where it was registered: (the environment of
    a ring, flowkit-spawn's options). An arm of before 0.15.1 names a tmux server and session."""
    old = arm.get("tmux") or {}
    if arm.get("where"):
        return {"FLOWKIT_TARGET": arm["where"]}, ["--target", arm["where"]]
    if old.get("socket"):
        return {"FLOWKIT_TMUX_SOCKET": old["socket"]}, ["--tmux-socket", old["socket"],
                                                        *(["--tmux-session", old["session"]] if old.get("session") else [])]
    return {}, []


def tmux_target():
    """The same for a poller of 0.15.0 or older that still runs when the watch is added: it reads
    the tmux server and session from the arm. Goes with the first minor after 2026-12-11."""
    sock = os.environ.get("FLOWKIT_TMUX_SOCKET") or os.path.basename(os.environ.get("TMUX", "").split(",")[0])
    if not sock:
        return {"socket": "", "session": ""}
    pane = ["-t", os.environ["TMUX_PANE"]] if os.environ.get("TMUX_PANE") else []
    code, out, _ = sh(["tmux", "-L", sock, "display-message", "-p", *pane, "#{session_name}"], None, timeout=5)
    return {"socket": sock, "session": out.strip() if code == 0 else ""}


def header_of(path):
    """A prompt header's Model, Effort and Needs: the lines before its first blank one."""
    try:
        head = Path(path).read_text(errors="replace").split("\n\n", 1)[0]
    except (OSError, TypeError):
        return {"model": "", "effort": "", "needs": []}
    found = {key: m.group(1) if (m := re.search(rf"(?:^|\s{{2,}}){key.capitalize()}:\s*(\S+)", head, re.M)) else ""
             for key in ("model", "effort")}
    return {**found, "needs": sorted(cap.prompt_header(path)[0])}


def prompt_of(session):
    """The prompt a session was started with, in flowkit-spawn's store: <name>.md, else its newest
    stamped copy; None for a session started by hand."""
    store = cap.claude_dir() / "spawned"
    if not session:
        return None
    if (store / f"{session}.md").is_file():
        return store / f"{session}.md"
    copies = sorted(p for p in store.glob("*.md") if re.fullmatch(rf"{re.escape(session)}-\d{{8}}-\d{{6}}\.md", p.name))
    return copies[-1] if copies else None


def github(arm):
    """Whether an arm asks GitHub, through gh; the other merge requests and refs ask GitLab, through glab."""
    return arm["kind"].startswith("github")


def named(name, base):
    """Whether name is one of base's arms: base, base#2, …"""
    return name == base or str(name).startswith(f"{base}#") and str(name)[len(base) + 1:].isdigit()


def kept(before, args):
    """What an arm keeps of the one before it (the red arm a fix slice answers, or itself armed
    again) when the call does not say: --ready or --until merged, and what to do on green. A fix
    slice's close registers the bare MR."""
    return {**({"ready": before["ready"], "until": before["until"]} if not args.ready and args.until == "green" else {}),
            **({"on_green": before["on_green"]} if not args.on_green and not args.on_green_run else {})}


def add(args):
    t = now()
    story = re.sub(r"[^a-z0-9]+", "-", args.story.lower()).lstrip("-")[:32].rstrip("-")  # as `handoff.py record`
    if not story:
        fail(f"--story needs a slug: {args.story!r}")
    if args.ready and args.until == "merged":
        fail("--ready ends a watch at green; with --until merged the MR is out of Draft at the push")
    tree = cap.toplevel(args.tree or os.getcwd())
    repo = cap.main_checkout(tree)
    repo_dir = re.sub(r"[^A-Za-z0-9._+-]", "-", os.path.basename(repo))
    branch = git(tree, "rev-parse", "--abbrev-ref", "HEAD")
    upstream = git(tree, "rev-parse", "@{u}")
    check = old = None
    if args.mr:
        m, pr = MR_URL.match(args.mr), PR_URL.match(args.mr)
        if m:
            target = {"kind": "gitlab-mr", "project": m.group(1), "iid": int(m.group(2)), "url": m.group(0)}
        elif pr:
            target = {"kind": "github-pr", "project": pr.group(2), "iid": int(pr.group(3)), "url": pr.group(0), "host": pr.group(1)}
        else:
            fail("--mr takes a merge request's URL (…/-/merge_requests/<iid>) or a pull request's (…/pull/<number>), "
                 f"not {args.mr!r}")
        base = args.name or f"{repo_dir}!{target['iid']}"
        old = next((a for a in arms() if a["state"] == "fixing" and a["story"] == story and a["base"] == base
                    and all(a.get(k) == v for k, v in target.items())), None)
        if old and branch != old["branch"]:
            # the fix slice's tree is a detached worktree, or the one checkout switched back: what
            # it pushed is origin/<the MR's branch>
            branch = old["branch"]
            upstream = git(tree, "rev-parse", f"refs/remotes/origin/{branch}")
        if not upstream:
            fail(f"nothing to watch: the branch in {tree} has no upstream (a watch arms the sha of @{{u}})")
        sha = upstream
        check = (cap.repo_section(cap.load_config(), tree) or {}).get("check")
    elif args.ref:
        origin = git(tree, "remote", "get-url", "origin")
        remote, host = REMOTE.match(origin), HOST.match(origin)
        project = args.project or (remote.group(1) if remote else "")
        sha = args.sha or git(tree, "rev-parse", f"refs/remotes/origin/{args.ref}")
        if not project or not sha:
            fail(f"--ref needs a project and a sha: {tree} has no origin/{args.ref} to take them from "
                 "(--project, --sha)")
        target = {"kind": "github-ref" if host and host.group(1) == "github.com" else "gitlab-ref", "project": project,
                  "ref": args.ref}
        base = args.name or f"{repo_dir}@{args.ref}"
    else:
        if not args.name:
            fail("--check needs --name: what the watch is called in the story file")
        target, base, sha = {"kind": "command", "command": args.check}, args.name, upstream
    if not NAME.match(base):
        fail(f"a watch's name is letters, digits and . _ ! # @ + -: {base!r}")
    tool = "" if target["kind"] == "command" else "gh" if github(target) else "glab"
    found = shutil.which(tool) if tool else None
    if tool and not found:
        fail(f"{tool} is not on the PATH: a {'GitHub' if tool == 'gh' else 'GitLab'} watch asks through it")
    on_green = {"run": args.on_green_run or "", "prompt": "", "repo": "", "name": ""}
    if args.on_green:
        prompt = os.path.abspath(args.on_green)
        if not os.path.isfile(prompt) or not os.path.getsize(prompt):
            fail(f"--on-green takes a prompt file: {args.on_green!r} is missing or empty")
        if args.repo and not os.path.isdir(args.repo):
            fail(f"--repo is where the --on-green prompt starts: {args.repo!r} is no directory")
        on_green.update(prompt=prompt, repo=cap.toplevel(args.repo or tree), name=args.on_green_name or Path(prompt).stem)
        if not SESSION.match(on_green["name"]):
            fail(f"a session's name is letters, digits and . _ -: {on_green['name']!r} (--on-green-name)")
    elif args.repo or args.on_green_name:
        fail("--repo and --on-green-name say where and under which name the --on-green prompt starts")
    me = {"sessionId": args.session, "name": ""} if args.session else cap.self_session(None)
    fresh = {**target, "base": base, "story": story, "sha": sha, "tree": tree, "repo": repo, "check": check,
             "branch": branch, "target": "",
             "glab": os.path.abspath(found) if tool == "glab" else "", **({"gh": os.path.abspath(found)} if tool == "gh" else {}),
             "until": args.until, "ready": args.ready,
             "by": {"session": me["sessionId"], "name": me["name"], "left": args.left or ""}, "where": where(), "tmux": tmux_target(),
             **header_of(prompt_of(me["name"])), "on_green": on_green, "worktree": "",
             "state": "pending", "phase": "pipeline", "armed": t, "targeted": t, "polled": None, "answer": "",
             "errors": 0, "seen": None, "blocked": None, "noted": [], "retargets": 0, "did": []}
    if old:
        fresh.update(kept(old, args), worktree=old["fix"].get("worktree") or "")
    arm = None
    for _ in range(5):  # an arm of this target still pending is armed again in place, under its own name
        same = next((a for a in arms() if a["state"] == "pending" and a["story"] == story and a["base"] == base
                     and all(a.get(k) == v for k, v in target.items())), None)
        if not same or (arm := commit(same, **{**fresh, **kept(same, args), "worktree": same["worktree"] or fresh["worktree"]})):
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
            cap.write_watches(t)
    if old and old["name"] != arm["name"]:  # the fix slice's close: the red arm it answers is over
        retire(old, t, f"armed again as {arm['name']}", "armed")
    took = pipeline_minutes(repo) if queued_tests(arm) else None
    testq(arm, t, "building", t + took * 60 if took else None)
    print(" · ".join(filter(None, [f"watching {arm['name']}", sha and f"sha {sha[:7]}", f"until {arm['until']}"])))
    ensure()


def listing(as_json):
    found = arms()
    if as_json:
        print(json.dumps(found, indent=2))
        return
    for a in found:
        more = f"{a['fix']['name']} {a['fix']['state']}" if a["state"] == "fixing" else \
            a["waits"]["base"] if a["state"] == "waiting-base" else "" if a["phase"] == "pipeline" else a["phase"]
        print(" · ".join(filter(None, [
            a["name"], a["story"], a["state"] + (f" ({more})" if more else ""),
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
    code, out, err = sh(["sh", "-c", cmd], arm["tree"], env=told(arm))
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
    found = {**verdict(head["status"], "pipeline", head.get("web_url") or ""), "pipeline": head.get("id")}
    seen = [head.get("id"), head["status"]]
    if found["is"] != "pending" and head.get("sha") != mr["sha"] and arm["seen"] != seen:
        # a merged-results pipeline carries another sha, so for the seconds after a push it cannot be
        # told from the last head's: its end counts at the second poll that sees it
        return answer("pending", f"{head['status']}, asked once more", seen=seen, **more)
    return {**found, **more}


def gh_api(arm, sha, number=None):
    """GitHub's answer to QUERY, through `gh api graphql`: the repository, with the commit as
    `object` and, for a number, its `pullRequest`."""
    owner, _, name = arm["project"].partition("/")
    query = QUERY % ((",$number:Int!", PULL) if number else ("", ""))
    code, out, err = sh([arm["gh"], "api", "graphql", "--hostname", arm.get("host") or "github.com", "-f", f"query={query}",
                         "-f", f"owner={owner}", "-f", f"name={name}", "-f", f"oid={sha}",
                         *(["-F", f"number={number}"] if number else [])], arm["tree"])
    if code != 0:
        raise Broken(err or (out.strip().splitlines() or [f"gh exited {code}"])[-1])
    try:
        repo = json.loads(out)["data"]["repository"]
    except (ValueError, KeyError, TypeError):
        repo = None
    if not isinstance(repo, dict):
        raise Broken("gh api: the answer names no repository")
    return repo


def checks(arm, commit, what, link, on, t):
    """A commit's checks, check runs and statuses alike, by GitHub's own roll-up of them. A red
    carries its failed checks as jobs (id, name, stage, url: an Actions job's id, the workflow as
    its stage); checks that were cancelled or wait for somebody are a report, as a cancelled
    pipeline is."""
    roll = commit.get("statusCheckRollup") if isinstance(commit, dict) else None
    state = roll.get("state") if isinstance(roll, dict) else None
    if not state:
        if t - arm["targeted"] > NO_PIPELINE:
            return answer("report", f"no checks for {on} after 15 min")
        return answer("pending", "no checks yet")
    if state == "SUCCESS":
        return answer("green", f"{what} green", link=link)
    if state not in ("FAILURE", "ERROR"):
        return answer("pending", f"{what} {state.lower()}")
    newest = {}  # a job run again is listed with its older tries: of one workflow run the newest counts
    nodes = [n for n in (roll.get("contexts") or {}).get("nodes") or [] if isinstance(n, dict)]
    for n in sorted(nodes, key=lambda n: n.get("startedAt") or n.get("createdAt") or ""):
        url = n.get("detailsUrl") or n.get("targetUrl") or ""
        run, job = re.search(r"/actions/runs/(\d+)", url), re.search(r"/job/(\d+)", url)
        flow = (((n.get("checkSuite") or {}).get("workflowRun") or {}).get("workflow") or {}).get("name") or ""
        name = n.get("name") or n.get("context") or ""
        newest[flow, name, run and run.group(1)] = (n.get("conclusion") or n.get("state") or "", {
            "id": job.group(1) if job else "", "name": name, "stage": flow, "url": url})
    jobs = [job for end, job in newest.values() if end in FAILED]
    stopped = next((end for end in STOPPED if end in {end for end, _ in newest.values()}), "")
    if stopped and not jobs:
        return answer("report", f"{what} {stopped.lower().replace('_', ' ')}", link=link)
    return answer("red", f"{what} red", link=link, jobs=jobs)


def checks_of(arm, ref, sha, t):
    """A ref's checks at a sha: pipeline_of() for GitHub."""
    commit = gh_api(arm, sha).get("object")
    return checks(arm, commit, f"{ref} checks", f"https://{arm.get('host') or 'github.com'}/{arm['project']}/commit/{sha}/checks",
                  f"{sha[:7]} on {ref}", t)


def pulling(arm, pr, more):
    """merging() for a pull request. GitHub does not say whether an auto-merge was cancelled or
    never set, and many repositories allow none: a green pull request with auto-merge off waits
    for somebody's merge, with a line, where a merge request's cancelled one ends the watch."""
    status = pr.get("mergeStateStatus") or ""
    if pr.get("mergeable") == "CONFLICTING" or status == "DIRTY":
        why = "merge blocked: conflict"
        return answer("report" if arm["blocked"] == why else "pending", why, blocked=why, **more)
    text = "waiting for approval" if pr.get("reviewDecision") == "REVIEW_REQUIRED" else \
        "waiting for the merge: changes requested" if pr.get("reviewDecision") == "CHANGES_REQUESTED" else \
        "waiting for the merge: auto-merge is off" if not pr.get("autoMergeRequest") else \
        "" if status in ("", "CLEAN", "UNSTABLE", "HAS_HOOKS", "UNKNOWN") else f"waiting for the merge: {status.lower()}"
    return answer("pending", text or "waiting for the merge", note=text, **more)


def pull_request(arm, t):
    """merge_request() for a GitHub pull request, in one ask: its state and the checks of the armed sha."""
    repo = gh_api(arm, arm["sha"], arm["iid"])
    pr = repo.get("pullRequest")
    if not isinstance(pr, dict) or not pr.get("state"):
        raise Broken("gh api: not a pull request")
    more = {"target": pr.get("baseRefName") or ""}
    head = pr.get("headRefOid") or arm["sha"]
    if pr["state"] == "MERGED":
        return answer("merged", "PR merged", head=head, **more, merged_sha=(pr.get("mergeCommit") or {}).get("oid") or head)
    if pr["state"] != "OPEN":
        return answer("report", f"PR {pr['state'].lower()}", **more)
    if head != arm["sha"]:  # someone pushed: the armed sha is no longer the head
        return answer("retarget", f"retarget {head[:7]}", sha=head, **more)
    if arm["phase"] == "merge":
        return pulling(arm, pr, more)
    if arm["check"]:
        return {**command(arm, arm["check"]), **more}
    return {**checks(arm, repo.get("object"), "checks", f"{arm['url']}/checks", arm["sha"][:7], t), **more}


def ask(arm, t):
    if arm["kind"] == "command":
        return command(arm, arm["command"])
    of = checks_of if github(arm) else pipeline_of
    if arm["kind"].endswith("-ref"):
        return of(arm, arm["ref"], arm["sha"], t)
    if arm["phase"] != "target":
        return (pull_request if github(arm) else merge_request)(arm, t)
    if not arm["target"]:
        raise Broken("the MR named no target branch")
    found = of(arm, arm["target"], arm["merged_sha"], t)
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
    if arm["state"] == "closing" or launching(arm):  # an end or a launch its poller or wrapper did not finish
        if held(arm):
            return
        if launching(arm) or wrapped(arm):
            if commit(arm, closer=None, claimed=time.time()):
                wrap(name)
        elif mine := commit(arm, closer=os.getpid(), claimed=time.time()):
            finish(mine, t)
        return
    if arm["state"] == "fixing":
        follow(arm, t)
    elif arm["state"] == "waiting-base":
        waiting(arm, t)
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
            log("green", new, t, sha=new["sha"], **took(new, t))
    elif kind == "merged" and arm["until"] == "merged":
        new = commit(arm, **keep, phase="target", merged_sha=found["merged_sha"], targeted=t)
        if new:
            log("merged", new, t, sha=found["merged_sha"])
            back, why = back_to_default(new, found["head"])
            if why:
                note(new, f"merged; {new['tree']} left on {new['branch']}: {why}", t)
            new = commit(new, back=back) or new
    elif kind == "red" and arm["kind"] in REQUESTS and arm["phase"] == "pipeline":
        red(arm, found, keep, t)
        return
    else:
        if kind == "merged":  # merged by hand before the watch saw green: nothing left to ready
            keep["ready"] = False
        close(arm, "green" if kind in ("green", "merged") else kind, found["text"], t, link=found.get("link") or "", **keep)
        return
    if new and t - new["armed"] > DEADLINE:
        close(new, "report", f"no result in 6 h (last answer: {found['text']})", t, op="deadline")


def took(arm, t):
    """What a green's log line says for the next arm's ETA: the repository and the minutes from the arm."""
    return {"repo": arm["repo"], "minutes": round((t - arm["armed"]) / 60)}


def close(arm, kind, what, t, op=None, link="", **keep):
    """An arm's end (green, red, report or dropped), claimed under its lock; the claimer alone
    finishes it, or the wrapper it starts when the end launches a session (an on-green prompt).
    False when another poll or an add changed the arm first."""
    launches = kind == "green" and bool(arm["on_green"]["prompt"])
    new = commit(arm, **keep, state="closing", closer=None if launches else os.getpid(), claimed=time.time(),
                 outcome={"kind": kind, "what": what, "at": t, "link": link})
    if not new:
        return False
    if kind != "dropped" and (kind != "green" or new["phase"] != "target"):  # a merged arm's green is in the log
        log(op or kind, new, t, what=what, sha=new["sha"], **(took(new, t) if kind == "green" else {}))
    if launches:
        wrap(new["name"])
    else:
        finish(new, t)
    return True


def launching(arm):
    """Whether the arm's fix slice is still to be launched: a wrapper's work."""
    return arm["state"] == "fixing" and (arm.get("fix") or {}).get("state") == "launching"


def held(arm):
    """Whether another process is at this arm's end or launch: a wrapper that is still starting, or
    a holder that lives and has claimed or beaten within TAKEOVER."""
    holder, age = arm.get("closer"), time.time() - arm["claimed"]
    if holder is None:
        return age <= STARTING
    return holder != os.getpid() and cap.pid_alive(holder) and age <= TAKEOVER


def wrapped(arm):
    """Whether a closing arm's end is a wrapper's to finish: it still has a session to start."""
    return arm["outcome"]["kind"] == "green" and bool(arm["on_green"]["prompt"]) and "prompt" not in arm["did"]


def wrap(name):
    """The detached wrapper of one launch (`watch.py launch <arm>`): flowkit-spawn can wait long (a
    load hold, a session's start-up), and the poller asks its other arms meanwhile."""
    out = watches() / "launched" / "wrapper.log"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "a") as f:
        subprocess.Popen([sys.executable, str(HERE / "watch.py"), "launch", name], stdin=subprocess.DEVNULL, stdout=f,
                         stderr=subprocess.STDOUT, start_new_session=True, env=scrubbed(), cwd=str(Path.home()))


def launch(name):
    """The wrapper: claims the arm, then starts the fix slice of a red, or finishes a green whose
    on-green prompt is to be started, and writes down what flowkit-spawn answered."""
    t = now()
    with locked(name):
        arm = read(name)
    if arm is None:
        return
    fixes = launching(arm)
    if not fixes and arm["state"] != "closing" or arm.get("closer") is not None and held(arm):
        return
    mine = commit(arm, closer=os.getpid(), claimed=time.time())
    if mine:
        (fix if fixes else finish)(mine, t)


def finish(arm, t):
    """What a claimed arm still owes: the MR out of Draft (--ready), the --on-green-run command, the
    --on-green prompt started, its fix worktree gone, its section through `handoff.py record`, then
    its file goes. A step done is written down first, so a poller that takes over a dead one's
    close repeats nothing."""
    out = arm["outcome"]
    green = out["kind"] == "green"
    if green and arm["ready"] and "ready" not in arm["did"]:
        argv = [arm["gh"], "pr", "ready", arm["url"]] if github(arm) else [arm["glab"], "mr", "update", str(arm["iid"]), "--ready"]
        code, _, err = sh(argv, arm["tree"], timeout=60)
        arm = commit(arm, did=arm["did"] + ["ready"],
                     ready_error="" if code == 0 else err or f"{'gh' if github(arm) else 'glab'} exited {code}")
        if arm is None:
            return
    if green and arm["on_green"]["run"] and "run" not in arm["did"]:
        code, said, err = sh(["sh", "-c", arm["on_green"]["run"]], arm["tree"] if os.path.isdir(arm["tree"]) else None,
                             timeout=RUN, env=told(arm))
        arm = commit(arm, did=arm["did"] + ["run"], ran={"exit": code, "last": (said.strip().splitlines() or [err])[-1]})
        if arm is None:
            return
    if green and arm["on_green"]["prompt"] and "prompt" not in arm["did"]:
        on = arm["on_green"]
        model, effort, capped = launch_as(arm, on["prompt"])
        if not os.path.isfile(on["prompt"]) or not os.path.isdir(on["repo"]):
            how, why = "", f"{on['prompt'] if os.path.isdir(on['repo']) else on['repo']} is gone"
        else:
            how, why, arm = spawn(arm, on["name"], on["repo"], on["prompt"], model, effort)
        arm = arm and commit(arm, did=arm["did"] + ["prompt"], next=f"{how} {on['name']}" if how else "", unstarted=why,
                             capped=capped)
        if arm is None:
            return
    if arm["worktree"] and "worktree" not in arm["did"]:
        arm = commit(arm, did=arm["did"] + ["worktree"], kept=unplant(arm))
        if arm is None:
            return
    if green:
        testq(arm, t, "ready to test", "")
    if not recorded(arm):
        record(arm, section(arm, t))
    with locked(arm["name"]):
        for suffix in ("json", "lock"):
            (watches() / f"{arm['name']}.{suffix}").unlink(missing_ok=True)
    log("done", arm, t, outcome=out["what"], end=out["kind"], minutes=round((out["at"] - arm["armed"]) / 60),
        **({"next": arm["next"]} if arm.get("next") else {}))
    cap.write_watches(t)


def record(arm, text):
    """A section into the story file through `handoff.py record`: the reporter's doorbell and the
    drain are record's; a record that fails still leaves the section, appended."""
    ring = {**os.environ, **reach(arm)[0]}
    code, _, _ = sh([sys.executable, str(HERE / "handoff.py"), "record", arm["story"]], None, timeout=300,
                    env=ring, stdin=text + "\n")
    if code != 0 and not recorded(arm):
        with story_file(arm["story"]).open("a") as f:
            f.write(f"\n{text}\n")


def retire(arm, t, how, end):
    """A fixing or waiting arm's end that owes no section of its own: its file and lock go, and
    `done` says how. False when the arm changed meanwhile."""
    with locked(arm["name"]):
        have = read(arm["name"])
        if have is None or have["rev"] != arm["rev"]:
            return False
        for suffix in ("json", "lock"):
            (watches() / f"{arm['name']}.{suffix}").unlink(missing_ok=True)
    log("done", arm, t, outcome=how, end=end, minutes=round((t - arm["armed"]) / 60))
    cap.write_watches(t)
    return True


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
    """An arm's section: its end, or, for an arm that stays `fixing`, its red with the fix slice as `Next:`."""
    done, for_me, still = [], [], "nothing"
    if arm["state"] == "fixing":
        f, out = arm["fix"], arm["red"]
        done.append(f"{'merge' if f.get('merge') else 'fix'} slice {f['name']} {f['state']} (auto"
                    + (f", effort {CAPPED[f['capped']]} for {f['capped']}" if f.get("capped") else "") + ") "
                    + ("in" if f["state"] == "started" else "for") + f" {f['tree']}" + (f": {f['queued']}" if f.get("queued") else ""))
        still = f"{out['what']}: {out['link'] or arm['url']}"
    else:
        out = arm["outcome"]
    if arm["state"] != "fixing" and out["kind"] == "green":
        done.append(out["what"])
        mr = "PR" if github(arm) else "MR"
        if "ready" in arm["did"] and arm["ready_error"]:
            done.append(f"{mr} still Draft")
            for_me.append(f"take the {mr} out of Draft, the watch could not ({arm['ready_error']}): {arm['url']}")
        elif "ready" in arm["did"]:
            done.append(f"{mr} ready")
        if arm.get("back"):
            done.append(arm["back"])
        on, ran = arm["on_green"], arm.get("ran")
        if ran:
            done.append(f"`{on['run']}` " + ("did not finish" if ran["exit"] is None else f"exit {ran['exit']}")
                        + (f": {ran['last']}" if ran["last"] else ""))
            if ran["exit"] != 0:
                for_me.append(f"the on-green command failed, see DONE: `{on['run']}`")
        if "prompt" in arm["did"] and arm["next"]:
            done.append(f"{arm['next'].split()[0]} {on['name']} (auto"
                        + (f", effort {CAPPED[arm['capped']]} for {arm['capped']}" if arm.get("capped") else "") + ")"
                        + (f": {arm['unstarted']}" if arm["unstarted"] else ""))
        elif "prompt" in arm["did"]:
            for_me.append(f"start {on['name']}, the watch could not ({arm['unstarted']}): "
                          f"`flowkit-spawn --story {arm['story']} --repo {on['repo']} --name {on['name']} {on['prompt']}`")
    elif arm["state"] != "fixing" and out["kind"] == "dropped":
        still = f"no longer watched: {arm.get('url') or arm['name']}"
    elif arm["state"] != "fixing":
        for_me.append(f"{out['what']}: {out['link'] or arm.get('url') or arm['name']}")
        still = out["what"]
    if "worktree" in arm["did"]:
        done.append(arm["kept"] or "the fix worktree removed")
    left = (arm["by"]["left"] or left_of(arm) or "see the close that registered this watch").strip()
    left = f"LEFT\n{left}" if "\n" in left else f"LEFT - {left.removeprefix('- ')}"
    mine = "FOR ME\n" + "\n".join(f"{n}. {text}" for n, text in enumerate(for_me, 1)) if len(for_me) > 1 else \
        f"FOR ME - {'1. ' + for_me[0] if for_me else 'nothing new'}"
    watch = " · ".join(filter(None, [f"Watch: {out['what']} {clock(out['at'])}", arm["sha"] and f"sha {arm['sha'][:7]}",
                                     f"{round((out['at'] - arm['armed']) / 60)} min", arm.get("url")]))
    return "\n".join([f"## {arm['name']} · {stamp(t)}", watch, f"Next: {arm.get('next') or 'none'}",
                      f"DONE - {'; '.join(done) or 'nothing new'}", mine, f"OPEN - {still}", left])


def told(arm):
    """What a command the watch runs for an arm is told, in its environment: the arm's name, sha, branch and MR."""
    return {**os.environ, "FLOWKIT_WATCH_NAME": arm["name"], "FLOWKIT_WATCH_SHA": arm["sha"],
            "FLOWKIT_WATCH_BRANCH": arm["branch"], "FLOWKIT_WATCH_MR": arm.get("url") or ""}


# ---- the sessions a watch starts ----

def launch_as(arm, prompt=None):
    """The model and effort of a session the watch starts: its prompt's own, else the registering
    slice's, an effort above xhigh capped, since nobody approved this launch: (model, effort, the
    effort it was capped from or "")."""
    own = header_of(prompt) if prompt else {}
    model, effort = own.get("model") or arm["model"], own.get("effort") or arm["effort"]
    return model, CAPPED.get(effort, effort), effort if effort in CAPPED else ""


def spawn(arm, name, tree, prompt, model, effort):
    """flowkit-spawn for a session the watch starts, its output kept in launched/<name>.log:
    ("started", "queued" or "", why it is queued or did not start, the arm, None once it changed
    under the wrapper). The claim is beaten while flowkit-spawn runs. A launch that a dead wrapper
    left half done counts as started when its session runs already (exit 4)."""
    again = "launching" in arm["did"]
    arm = commit(arm, did=arm["did"] + ([] if again else ["launching"]), claimed=time.time())
    if arm is None:
        return "", "", None
    argv = [str(HERE.parent / "bin" / "flowkit-spawn"), "--from-watch", arm["name"], "--story", arm["story"],
            "--repo", str(tree), "--name", name, *(["--model", model] if model else []),
            *(["--effort", effort] if effort else [])]
    argv += [*reach(arm)[1], str(prompt)]
    out = watches() / "launched" / f"{name}.log"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "ab") as f:
        f.write(f"--- {stamp(time.time())} · {' '.join(argv)}\n".encode())
        f.flush()
        start = f.tell()
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, env=scrubbed(),
                                    cwd=str(tree))
        except OSError as e:
            return "", str(e), arm
        while True:
            try:
                code = proc.wait(timeout=BEAT)
                break
            except subprocess.TimeoutExpired:
                arm = commit(arm, claimed=time.time()) or arm
    said = [line for line in out.read_bytes()[start:].decode(errors="replace").splitlines() if line.strip()]
    if code == 0 and any(line.startswith("SPAWNED ") for line in said) or code == 4 and again:
        return "started", "", arm
    if code == cap.QUEUED:
        line = next((line for line in reversed(said) if line.startswith("QUEUED ")), "")
        return "queued", line.partition(" · id=")[2].partition(" · ")[2], arm
    return "", f"flowkit-spawn exit {code}, {EXITS.get(code) or (said[-1] if said else 'no output')}; its output: {out}", arm


# ---- red ----

def reds(arm):
    """How many reds the log holds for this MR, under any of its arms' names: the count outlives the arms' files."""
    return sum(ev["event"] == "watch" and ev.get("op") == "red" and ev.get("story") == arm["story"]
               and named(ev.get("name"), arm["base"]) for ev in cap.events())


def failed(arm, red):
    """A red's failed jobs (id, name, stage, url): a pull request's came with its answer, a
    pipeline's are asked for; none when they cannot be asked: a repository's own check, glab down."""
    if "jobs" in red:
        return red["jobs"]
    pipeline = red.get("pipeline")
    if not pipeline or arm["check"]:
        return []
    try:
        rows = glab_api(arm, f"projects/{quote(arm['project'], safe='')}/pipelines/{pipeline}/jobs?scope[]=failed&per_page=100")
    except Broken:
        return []
    return [{"id": r.get("id"), "name": r.get("name") or "", "stage": r.get("stage") or "", "url": r.get("web_url") or ""}
            for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def red(arm, found, keep, t):
    """A merge request's red. The first one starts one fix slice, unless the MR's target is the
    branch of another arm that is red itself: that base is fixed in its own story, and this arm
    waits for it (`waiting-base`). A second red is the user's: a section, no slice."""
    link = found.get("link") or ""
    if reds(arm):
        names = ", ".join(job["name"] for job in failed(arm, found))
        close(arm, "red", "red again after one fix" + (f": {names}" if names else ""), t, link=link, **keep)
        return
    was = {"what": found["text"], "link": link, "pipeline": found.get("pipeline"), "at": t,
           **({"jobs": found["jobs"]} if "jobs" in found else {})}
    base = next((a for a in arms() if a["name"] != arm["name"] and a["kind"] == arm["kind"] and a["project"] == arm["project"]
                 and a["branch"] == keep["target"] and a["state"] in ("fixing", "waiting-base")), None)
    if base:
        new = commit(arm, **keep, state="waiting-base", red=was,
                     waits={"base": base["base"], "story": base["story"], "branch": base["branch"]})
        if new:
            log("red", new, t, what=was["what"], sha=new["sha"], waits=base["name"])
            note(new, f"{was['what']}; waiting for its base {base['name']}, which is red", t)
            testq(new, t, "fixing", "")
        return
    new = commit(arm, **keep, red=was, **fixing(arm, t))
    if new:
        log("red", new, t, what=was["what"], sha=new["sha"])
        log("fix", new, t, fix=new["fix"]["name"])
        wrap(new["name"])


def fixing(arm, t, merge=""):
    """The fields of an arm whose fix slice is to be launched: `<story>-fix-<n>`, n counting the
    story's fix slices in the log. merge: the base branch a waiting arm's slice merges in instead."""
    n = 1 + sum(ev["event"] == "watch" and ev.get("op") == "fix" and ev.get("story") == arm["story"] for ev in cap.events())
    return {"state": "fixing", "closer": None, "claimed": time.time(),
            "fix": {"name": f"{arm['story']}-fix-{n}", "state": "launching", "at": t, "merge": merge}}


def fix(arm, t):
    """The wrapper's launch of a fix slice: its tree, its brief, flowkit-spawn. Then the red's
    section, `Next: started|queued <session>`, while the arm stays `fixing`; or a report when no
    session started."""
    f, was = arm["fix"], arm["red"]
    try:
        tree, planted, why = place(arm)
    except Stuck as e:
        close(arm, "report", f"{was['what']}; no fix slice started: {e}", t, link=was["link"])
        return
    model, effort, capped = launch_as(arm)
    arm = commit(arm, fix={**f, "tree": tree, "worktree": planted, "why": why, "capped": capped},
                 worktree=planted or arm["worktree"])
    if arm is None:
        return
    prompt = cap.claude_dir() / "spawned" / f"{f['name']}.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(brief(arm, model, effort))
    how, why, arm = spawn(arm, f["name"], tree, prompt, model, effort)
    if arm is None:
        return
    if not how:
        close(arm, "report", f"{was['what']}; the fix slice did not start ({why})", t, link=was["link"])
        return
    arm = commit(arm, fix={**arm["fix"], "state": how, "queued": why}, next=f"{how} {f['name']}", closer=None)
    if arm is None:
        return
    testq(arm, t, "fixing", "")
    if not recorded(arm):
        record(arm, section(arm, t))


def brief(arm, model, effort):
    """The fix slice's prompt, from fix-brief.md: head, the red or the base to merge in, how its
    tree is to be used, the last close of the story and its Chain end."""
    blocks, name = {}, None
    for line in (HERE / "fix-brief.md").read_text().splitlines():
        if line.startswith("=== "):
            name = line[4:].strip()
            blocks[name] = []
        elif name:
            blocks[name].append(line)
    f, was = arm["fix"], arm["red"]
    jobs = failed(arm, was)
    hub = github(arm)
    try:
        story = story_file(arm["story"]).read_text(errors="replace")
    except OSError:
        story = ""
    closes = [part.strip() for part in re.split(r"^(?=## )", story, flags=re.M) if re.search(r"^Session: ", part, re.M)]
    end = re.search(r"^Chain end:[ \t]*(.+)$", re.split(r"^## ", story, maxsplit=1, flags=re.M)[0], re.M)
    end = end.group(1).strip() if end else "none is written in the story file: push nothing but the fix"
    line = "   ".join(filter(None, [model and f"Model: {model}", effort and f"Effort: {effort}"]))
    values = {
        "tree": f["tree"], "session": f["name"], "story": arm["story"], "arm": arm["name"],
        "model": f"{line}{' (auto: ' + f['capped'] + ' is capped for a launch nobody approved)' if f['capped'] else ''}\n" if line else "",
        "by": arm["by"]["name"] or arm["by"]["session"] or "a session started by hand",
        "url": arm["url"], "sha": arm["sha"][:12], "what": was["what"], "link": was["link"] or arm["url"],
        "project": arm["project"], "target": arm["target"] or "the target branch", "branch": arm["branch"],
        "base": f.get("merge") or "", "why": f.get("why") or "",
        "push": f"git push origin HEAD:refs/heads/{arm['branch']}" if f["worktree"] else "git push",
        "request": "pull request" if hub else "merge request",
        "log": f"gh run view --job <job id> --log-failed -R {arm['project']}" if hub else f"glab ci trace <job id> -R {arm['project']}",
        "retry": f"gh run rerun --job <job id> -R {arm['project']}" if hub else f"glab ci retry <job id> -R {arm['project']}",
        "jobs": "\n".join(f"- {j['name']} ({j['stage']}, job {j['id']}): {j['url']}" if j["id"] else f"- {j['name']}: {j['url']}"
                          for j in jobs)
                or (f"- the repository's own check said: {was['what']}" if arm["check"] else f"- not listed: see {was['link'] or arm['url']}"),
        "needs": (f"The slice that pushed held {', '.join(arm['needs'])}: take one only for a step that uses it "
                  "(`flowkit-capacity take <name>`) and give it back after.\n\n") if arm["needs"] else "",
        "last": "\n".join(closes[-1].splitlines()[:60]) if closes else "(the story file holds no close yet)",
        "chain_end": end,
    }
    how = "worktree" if f["worktree"] else "checkout" if f["why"] == "checkout" else "tree"
    parts = ["head", "base" if f.get("merge") else "red", how, *(["merge"] if re.match(r"merge\b", end) else []), "tail"]
    text = "\n\n".join("\n".join(blocks[part]).strip("\n") for part in parts)
    return re.sub(r"\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), text) + "\n"


def place(arm):
    """Where the fix slice works: (tree, the worktree made for it or "", why). The registered tree
    when it is free and still on the MR's branch; the one checkout of a `checkouts = 1` repository,
    where the gate queues the launch behind the lease's holder; else a fresh worktree, detached at
    origin/<branch> (the branch itself may be checked out elsewhere). Stuck when there is none."""
    f, tree, branch = arm["fix"], arm["tree"], arm["branch"]
    if f.get("tree") and os.path.isdir(f["tree"]):  # a wrapper before this one got that far
        return f["tree"], f["worktree"], f["why"]
    main = arm["repo"]
    if not os.path.isdir(main):
        raise Stuck(f"{main} is gone")
    one = cap.checkout(cap.load_config(), main)
    if one:
        who = "" if one in cap.leases() else sessions_in(main)[0]
        if who:
            raise Stuck(f"{who}, in the repository's one checkout, without holding {one}")
        return main, "", "checkout"
    busy = "the tree is gone" if not os.path.isdir(tree) else \
        "the tree is on another branch" if git(tree, "rev-parse", "--abbrev-ref", "HEAD") != branch else \
        taken(tree) or ("the tree has uncommitted changes" if dirty(tree) else "")
    if not busy:
        return tree, "", ""
    if branch in ("", "HEAD"):
        raise Stuck(f"{busy}, and the watch knows no branch to make a worktree of")
    path = os.path.join(main, ".claude", "worktrees", f["name"])
    sh(["git", "-C", main, "worktree", "prune"], None)
    sh(["git", "-C", main, "fetch", "origin", branch], None, timeout=120)
    code, _, err = sh(["git", "-C", main, "worktree", "add", "--detach", path, f"origin/{branch}"], None, timeout=120)
    if code != 0:
        raise Stuck(f"{busy}, and git worktree add failed: {err}")
    return path, path, busy


def dirty(tree):
    """Uncommitted changes, an untracked `.claude/` aside (a session's worktrees live there), as flowkit-spawn reads it."""
    code, out, _ = sh(["git", "-C", str(tree), "status", "--porcelain"], None)
    return code != 0 or any(line and not line.startswith("?? .claude/") for line in out.splitlines())


def sessions_in(tree):
    """Who works in the tree ("" for nobody), and the pids of the sessions there that do not count:
    a reporter, and a slice whose close is recorded unless it is busy again. Sessions unknown
    counts as somebody."""
    rows = cap.snapshot()
    if rows is None:
        return "the sessions are unknown (`claude agents` did not answer)", set()
    _, closed = cap.stories()
    who, idle = "", set()
    for row in rows:
        name = row.get("name") or ""
        if not row.get("pid") or not row.get("cwd") or cap.toplevel(row["cwd"]) != os.path.realpath(tree):
            continue
        if name.endswith("-report") or row["sessionId"] in closed and row.get("status") != "busy":
            idle.add(row["pid"])
        else:
            who = who or f"session {name or row['sessionId'][:8]} works there"
    return who, idle


def taken(tree):
    """Why a tree is not free for the watch to work in, or "": a live session there, a launch
    queued or starting for it, a resource of its repository held for the user's testing, or any
    other process with its cwd in it (a Metro, a dev server). In a `checkouts = 1` repository the
    checkout's lease stands for the process test: its dev server always runs there."""
    who, idle = sessions_in(tree)
    if who:
        return who
    real = os.path.realpath(tree)
    for entry in cap.entries():
        if entry.get("tree") and os.path.realpath(entry["tree"]) == real:
            return f"{entry.get('name') or 'a launch'} is queued for it"
    for mark in cap.markers():
        if mark["alive"] and mark.get("tree") and os.path.realpath(mark["tree"]) == real:
            return f"{mark.get('name') or 'a launch'} is starting there"
    cfg, leases = cap.load_config(), cap.leases()
    one = cap.checkout(cfg, real)
    if one:
        return f"{one} is held by {leases[one].get('name') or leases[one]['session']}" if one in leases else ""
    mine = sorted(n for n in (cap.repo_section(cfg, real) or {"resources": {}})["resources"]
                  if n in leases and leases[n]["session"] == cap.ME and leases[n].get("until", 0) > time.time())
    if mine:
        return f"{', '.join(mine)} is held for the user's testing"
    return process_in(real, idle)


def process_in(tree, idle):
    """A process with its cwd in the tree, as "<command> (pid <n>) runs there", or "". Not counted:
    a bare shell, this process with its parents and children, and what the sessions in idle run.
    An lsof that does not answer counts as a process."""
    _, out, _ = sh(["lsof", "-a", "-d", "cwd", "-Fn"], None)
    if not out:
        return "the processes are unknown (lsof did not answer)"
    inside, pid = [], None
    for line in out.splitlines():
        if line[:1] == "p" and line[1:].isdigit():
            pid = int(line[1:])
        elif line[:1] == "n" and pid and (line[1:] == tree or line[1:].startswith(tree + os.sep)):
            inside.append(pid)
    if not inside:
        return ""
    parent, command = {}, {}
    for line in sh(["ps", "-axo", "pid=,ppid=,comm="], None)[1].splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3 and fields[0].isdigit() and fields[1].isdigit():
            parent[int(fields[0])], command[int(fields[0])] = int(fields[1]), os.path.basename(fields[2]).lstrip("-")

    def line_of(pid):
        up = []
        while pid and pid not in up and len(up) < 64:
            up.append(pid)
            pid = parent.get(pid, 0)
        return up

    own = set(line_of(os.getpid()))
    for pid in sorted(inside):
        up = set(line_of(pid))
        if pid in command and pid not in own and os.getpid() not in up and not idle & up and command[pid] not in SHELLS:
            return f"{command[pid]} (pid {pid}) runs there"
    return ""


def unplant(arm):
    """The fix worktree goes with its arm's end, unless a session still works in it or it holds
    what nobody committed: "" once it is gone, else what the section says."""
    path = arm["worktree"]
    if not os.path.isdir(path):
        return ""
    who = sessions_in(path)[0]
    if who:
        return f"worktree {path} kept: {who}"
    code, _, err = sh(["git", "-C", arm["repo"], "worktree", "remove", path], None, timeout=60)
    sh(["git", "-C", arm["repo"], "worktree", "prune"], None)
    return "" if code == 0 else f"worktree {path} kept: {err}"


def follow(arm, t):
    """A fixing arm after its launch waits for the fix slice's close to arm the next name (`add`
    retires it then); no deadline runs meanwhile. A fix slice that closed without arming, or that
    is gone with no close, ends the arm with an interim line."""
    f = arm["fix"]
    if not recorded(arm):  # the wrapper went between the launch and the section
        record(arm, section(arm, t))
    try:
        closed = re.search(rf"^## {re.escape(f['name'])} · ", story_file(arm["story"]).read_text(errors="replace"), re.M)
    except OSError:
        closed = None
    if closed:
        how, end = f"{f['name']} closed without arming the watch again", "closed"
    elif t - f["at"] > GONE and int((t - f["at"]) // POLL) % 10 == 0 and gone(f["name"]):
        how, end = f"{f['name']} is gone with no close", "gone"
    else:
        return
    kept = unplant(arm) if arm["worktree"] else ""
    if retire(arm, t, how, end):
        note(arm, "; ".join(filter(None, [how, kept])), t)


def gone(name):
    """Whether a session the watch started is neither running, queued nor starting; unknown is not gone."""
    rows = cap.snapshot()
    return rows is not None and not any(r.get("name") == name and r.get("pid") for r in rows) \
        and not any(e.get("name") == name for e in cap.entries()) \
        and not any(m["alive"] and m.get("name") == name for m in cap.markers())


def waiting(arm, t):
    """A waiting-base arm: while its base has an arm open it waits, with no deadline. The base's
    green starts one slice that merges the base in, through the fix slice's path; any other end
    of the base is reported with this arm's red."""
    base, was = arm["waits"], arm["red"]
    if any(a["base"] == base["base"] and a["story"] == base["story"] for a in arms()):
        return
    ends = [ev for ev in cap.events() if ev["event"] == "watch" and ev.get("op") == "done" and ev["t"] >= was["at"]
            and ev.get("story") == base["story"] and named(ev.get("name"), base["base"])]
    if ends and ends[-1].get("end") == "green":
        new = commit(arm, **fixing(arm, t, merge=base["branch"]))
        if new:
            log("fix", new, t, fix=new["fix"]["name"], merge=base["branch"])
            wrap(new["name"])
    else:
        close(arm, "report", f"{was['what']}; its base {base['base']} ended with no green", t, link=was["link"])


# ---- the test queue ----

def queued_tests(arm):
    """The test-queue lines that name this arm's MR, as (story, item): its URL or `<repo dir>!<iid>`
    in any story's queue, a bare `!<iid>` in its own story's."""
    if arm["kind"] not in REQUESTS:
        return []
    own = re.sub(r"[^A-Za-z0-9._+-]", "-", os.path.basename(arm["repo"])) + f"!{arm['iid']}"
    found = []
    for path in sorted((cap.claude_dir() / "stories").glob("*.tests.md")):
        story = path.name[:-len(".tests.md")]
        found += [(story, line[0]) for line in cap.read_tests(story)[1]
                  if line[1] and (arm["url"] in line[1] or line[1] == own or story == arm["story"] and line[1] == f"!{arm['iid']}")]
    return found


def testq(arm, t, state, eta=None):
    """The MR's test-queue lines moved: `building` at the arm, `ready to test` on green, `fixing` on red."""
    for story, item in queued_tests(arm):
        with cap.lock():
            cap.testq_set(story, item, t, state=state, eta=eta)


def pipeline_minutes(repo):
    """A repository's median minutes from an arm to its green, from the log; None under three greens."""
    spans = [ev["minutes"] for ev in cap.events() if ev["event"] == "watch" and ev.get("op") == "green"
             and ev.get("repo") == repo and isinstance(ev.get("minutes"), (int, float))]
    return statistics.median(spans) if len(spans) >= cap.MIN_SPANS else None


# ---- after the merge ----

def back_to_default(arm, head):
    """The mechanical back-to-default once the MR merged: only in a main checkout still on the MR's
    branch, clean and free (taken). Switches to the default branch, pulls fast-forward
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
    who = taken(tree)
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
        cap.write_json(watches() / "poller.kinds", {"pid": os.getpid(), "kinds": KINDS})  # before the pid: never a pid without them
        fd = os.open(pidfile(), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        return True


def asks(pid):
    """The kinds of arm the poller that runs can ask: poller.kinds, which it wrote when it took
    poller.pid; a poller of 0.17.0 or older wrote none and asks GitLab for all that is no command."""
    try:
        said = json.loads((watches() / "poller.kinds").read_text())
        return set(said["kinds"]) if said["pid"] == pid else set(KINDS[:3])
    except (OSError, ValueError, KeyError, TypeError):
        return set(KINDS[:3])


def start():
    subprocess.Popen([sys.executable, str(HERE / "watch.py"), "run"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, env=scrubbed(),
                     cwd=str(Path.home()))


def ensure():
    """The tick, every `handoff.py record`, every flowkit-spawn end and every add call this: a
    watch that is open with no poller gets one. A poller that cannot ask an open arm's kind (an
    older flowkit's, still running after an update) is ended first: the one that starts takes over
    whatever it was in the middle of, as it would a dead poller's."""
    found, pid = arms(), poller()
    if pid and not {arm["kind"] for arm in found} <= asks(pid) \
            and re.search(r"watch\.py run\s*$", sh(["ps", "-o", "command=", "-p", str(pid)], None)[1]):
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
        for _ in range(100):
            if not cap.pid_alive(pid):
                break
            time.sleep(0.1)
        pid = poller()
    if found and pid is None:
        start()


def run():
    """The one poller: asks every open arm once a minute, keeps the machine awake while an arm is
    asked or ending (never for one that only waits for its fix slice or its base), touches its
    heartbeat every loop and is gone when no arm is open."""
    for key in set(os.environ) - set(scrubbed()):
        del os.environ[key]
    if not take():
        print(f"a poller runs already (pid {poller()})")
        return
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    awake = None
    every = float(os.environ.get("FLOWKIT_WATCH_POLL") or POLL)
    asked = {}
    try:
        while True:
            try:
                os.utime(pidfile())
            except OSError:
                if not take():
                    return
            found = arms()
            names = [arm["name"] for arm in found]
            asking = any(arm["state"] in ("pending", "closing") or launching(arm) for arm in found)
            if asking and awake is None and shutil.which("caffeinate"):
                awake = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())], stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif not asking and awake:
                awake.terminate()
                awake = None
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
    what.add_argument("--mr", "--pr")
    what.add_argument("--ref")
    what.add_argument("--check")
    for flag in ("--name", "--sha", "--project", "--tree", "--left", "--session", "--on-green-run", "--on-green", "--repo",
                 "--on-green-name"):
        a.add_argument(flag)
    a.add_argument("--ready", action="store_true")
    a.add_argument("--until", choices=("green", "merged"), default="green")
    sub.add_parser("list").add_argument("--json", action="store_true")
    sub.add_parser("drop").add_argument("name")
    sub.add_parser("poll").add_argument("name", nargs="?")
    sub.add_parser("run")
    sub.add_parser("ensure")
    sub.add_parser("launch").add_argument("name")
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
    elif args.cmd == "launch":
        launch(args.name)
    else:
        ensure()


if __name__ == "__main__":
    main()
