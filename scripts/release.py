#!/usr/bin/env python3
"""Release one flowkit plugin from the branch it was built on, and update this machine to it.

    scripts/release.py <plugin> patch|minor [--dry-run]
    scripts/release.py tags [--create]

A release refuses a dirty tree, a branch behind its upstream, a `main` it cannot fast-forward and
red tests (scripts/test.sh); then it bumps `version` in plugins/<plugin>/.claude-plugin/plugin.json
alone, commits that file, tags `<plugin>-v<version>`, fast-forwards `main` to the branch, pushes
`main` and the tag to `origin` together, and runs `claude plugin marketplace update` and
`claude plugin update <plugin>@<marketplace>`. Every step is printed as it runs; `--dry-run`
prints them and changes nothing (it neither fetches nor runs the tests).

`tags` lists every version a plugin.json ever carried on `main` with no tag; `--create` tags each
at the commit that set it. Pushing those tags is left to the caller.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAIN = "main"
REMOTE = "origin"
VERSION_LINE = re.compile(r'("version"\s*:\s*")(\d+)\.(\d+)\.(\d+)(")')


class Refused(Exception):
    pass


def git(*args, check=True):
    done = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True)
    if check and done.returncode != 0:
        raise Refused(f"git {' '.join(args)} failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout.strip()


def is_ancestor(older, newer):
    return subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", older, newer]).returncode == 0


def bumped(version, step):
    major, minor, patch = (int(n) for n in version.split("."))
    return f"{major}.{minor + 1}.0" if step == "minor" else f"{major}.{minor}.{patch + 1}"


def plugin_json(plugin):
    return ROOT / "plugins" / plugin / ".claude-plugin" / "plugin.json"


def current_version(path):
    match = VERSION_LINE.search(path.read_text())
    if not match:
        raise Refused(f"{path.relative_to(ROOT)} has no x.y.z version")
    return ".".join(match.group(2, 3, 4))


def checks(plugin, dry_run):
    """Everything that must hold before a release changes anything. Returns (branch, upstream)."""
    if not plugin_json(plugin).is_file():
        raise Refused(f"no plugin named {plugin!r} (no plugins/{plugin}/.claude-plugin/plugin.json)")
    if git("status", "--porcelain"):
        raise Refused("the working tree is not clean (git status --porcelain prints something)")
    branch = git("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if not branch:
        raise Refused("HEAD is detached: check out the branch to release from")
    if dry_run:
        print(f"    would fetch {REMOTE} with its tags; checking against the last fetch")
    else:
        print(f"    git fetch --tags {REMOTE}")
        git("fetch", "--tags", REMOTE)
    upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}", check=False)
    if upstream and git("rev-list", "--count", f"HEAD..{upstream}") != "0":
        raise Refused(f"{branch} is behind its upstream {upstream}: pull or rebase first")
    remote_main = f"{REMOTE}/{MAIN}"
    if git("rev-parse", "--verify", "--quiet", remote_main, check=False) and not is_ancestor(remote_main, "HEAD"):
        raise Refused(f"{branch} does not contain {remote_main}: rebase it onto {remote_main} first")
    if branch != MAIN:
        if git("rev-parse", "--verify", "--quiet", MAIN, check=False) and not is_ancestor(MAIN, "HEAD"):
            raise Refused(f"{MAIN} cannot fast-forward to {branch}: {MAIN} has commits {branch} lacks")
        for line in git("worktree", "list", "--porcelain").splitlines():
            if line == f"branch refs/heads/{MAIN}":
                raise Refused(f"{MAIN} is checked out in another working tree: release from {MAIN} there, or switch it away")
    return branch, upstream


def release(plugin, step, dry_run):
    path = plugin_json(plugin)
    print(f"[1] checks: clean tree, branch not behind its upstream, {MAIN} can fast-forward")
    branch, upstream = checks(plugin, dry_run)
    old = current_version(path)
    new = bumped(old, step)
    tag = f"{plugin}-v{new}"
    if git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}", check=False) or git("ls-remote", "--tags", REMOTE, f"refs/tags/{tag}"):
        raise Refused(f"tag {tag} exists already")
    print(f"    ok: {branch}{f' (upstream {upstream})' if upstream else ''}, {plugin} {old} -> {new}")

    print("[2] tests: scripts/test.sh")
    if dry_run:
        print("    would run every suite and stop here on red")
    elif subprocess.run([str(ROOT / "scripts" / "test.sh")], cwd=ROOT).returncode != 0:
        raise Refused("the tests are red")
    else:
        print("    ok: green")

    print(f"[3] bump: {path.relative_to(ROOT)} version {old} -> {new}")
    if not dry_run:
        path.write_text(VERSION_LINE.sub(lambda m: f"{m.group(1)}{new}{m.group(5)}", path.read_text(), count=1))
        if git("status", "--porcelain", "--untracked-files=all").splitlines() != [f"M {path.relative_to(ROOT)}"]:
            raise Refused("the bump changed more than the plugin.json")

    print(f"[4] commit: \"{plugin} {new}\"")
    if not dry_run:
        git("commit", "--quiet", "-m", f"{plugin} {new}", "--", str(path.relative_to(ROOT)))

    print(f"[5] tag: {tag}")
    if not dry_run:
        git("tag", tag)

    if branch != MAIN:
        print(f"[6] fast-forward {MAIN} to {branch}")
        if not dry_run:
            git("fetch", "--quiet", ".", f"{branch}:{MAIN}")
    else:
        print(f"[6] fast-forward: none, the release is on {MAIN}")

    print(f"[7] push: {MAIN} and {tag} to {REMOTE}, together")
    if not dry_run:
        git("push", "--quiet", "--atomic", REMOTE, f"refs/heads/{MAIN}", f"refs/tags/{tag}")

    marketplace = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())["name"]
    for number, cmd in (("8", ["claude", "plugin", "marketplace", "update", marketplace]),
                        ("9", ["claude", "plugin", "update", f"{plugin}@{marketplace}"])):
        print(f"[{number}] this machine: {' '.join(cmd)}")
        if not dry_run and subprocess.run(cmd, stdin=subprocess.DEVNULL).returncode != 0:
            raise Refused(f"{' '.join(cmd)} failed; {tag} is pushed, run it again by hand")

    print(f"{'would release' if dry_run else 'released'} {tag}"
          + ("" if dry_run else "; sessions already running keep the old version until they restart"))


def missing_tags(create):
    """Every version a plugin.json carried on main, with the commit that set it, lacking a tag."""
    missing = []
    for path in sorted((ROOT / "plugins").glob("*/.claude-plugin/plugin.json")):
        rel = str(path.relative_to(ROOT))
        plugin = path.parent.parent.name
        previous = None
        for commit in git("rev-list", "--reverse", MAIN, "--", rel).splitlines():
            shown = subprocess.run(["git", "-C", str(ROOT), "show", f"{commit}:{rel}"], capture_output=True, text=True)
            match = VERSION_LINE.search(shown.stdout) if shown.returncode == 0 else None
            version = ".".join(match.group(2, 3, 4)) if match else None
            if version and version != previous:
                tag = f"{plugin}-v{version}"
                if not git("rev-parse", "--verify", "--quiet", f"refs/tags/{tag}", check=False):
                    missing.append((tag, commit))
            previous = version
    for tag, commit in missing:
        print(f"{tag} {commit[:7]} {'tagged' if create else 'missing'}")
        if create:
            git("tag", tag, commit)
    if not missing:
        print("every version has its tag")
    return missing


def main(argv=None):
    parser = argparse.ArgumentParser(prog="release.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("plugin", help="the plugin to release, or `tags`")
    parser.add_argument("step", nargs="?", choices=("patch", "minor"))
    parser.add_argument("--dry-run", action="store_true", help="print the steps, change nothing")
    parser.add_argument("--create", action="store_true", help="with `tags`: create the missing tags")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)  # steps, git's and the tests' output in order
    try:
        if args.plugin == "tags":
            missing = missing_tags(args.create)
            return 1 if missing and not args.create else 0
        if not args.step:
            parser.error("name the step: patch or minor")
        release(args.plugin, args.step, args.dry_run)
        return 0
    except Refused as refusal:
        print(f"refused: {refusal}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
