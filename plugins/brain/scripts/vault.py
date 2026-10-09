#!/usr/bin/env python3
"""Find and register the vault the brain skills work on.

  vault.py resolve <data-dir>      print the vault path: $BRAIN_DIR, else the path file
                                   <data-dir>/vault-path, else the current directory when
                                   it is a vault; exit 1 with a message when none is
  vault.py init <data-dir> <plugin-root> <path>
                                   what /brain:init does: create the vault at <path> from
                                   <plugin-root>/template when the path is absent or empty,
                                   start git there on main, write the vault's
                                   .claude/settings.json and the path file; an existing vault
                                   is only registered; exit 1 on a non-empty directory that
                                   is not a vault

A directory is a vault when it holds wiki/ and index.md.
"""
import json
import os
import shutil
import subprocess
import sys

# Where a vault's .claude/settings.json finds the marketplace. None until the first release
# fills in the published address, e.g. {"source": "github", "repo": "OWNER/flowkit"}; until
# then a marketplace added from a directory is registered by that directory's path.
MARKETPLACE_SOURCE = None
MARKETPLACE = "flowkit"
PLUGIN = "brain"

REQUIRED = ("wiki", "index.md")
OPTIONAL = ("raw", "log.md", "hot.md", "inbox.md", "_me.md", "templates")
PATH_FILE = "vault-path"


def fail(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def absolute(path):
    return os.path.abspath(os.path.expanduser(path))


def missing(path, parts):
    return [part for part in parts if not os.path.exists(os.path.join(path, part))]


def is_vault(path):
    return os.path.isdir(path) and not missing(path, REQUIRED)


def resolve(data_dir):
    env = os.environ.get("BRAIN_DIR", "").strip()
    if env:
        if is_vault(absolute(env)):
            return absolute(env)
        fail(f"BRAIN_DIR={env} is not a vault (it needs wiki/ and index.md). Fix or unset it.")
    path_file = os.path.join(data_dir, PATH_FILE)
    if os.path.exists(path_file):
        with open(path_file, encoding="utf-8") as handle:
            saved = handle.read().strip()
        if is_vault(saved):
            return saved
        fail(f"{path_file} points at {saved}, which is not a vault any more. Run /brain:init with the right path.")
    if is_vault(os.getcwd()):
        return os.getcwd()
    fail("No vault configured. Run /brain:init <path> to create or register one, or set BRAIN_DIR.")


def marketplace_source(plugin_root):
    if MARKETPLACE_SOURCE:
        return MARKETPLACE_SOURCE
    root = os.path.dirname(os.path.dirname(absolute(plugin_root)))
    if os.path.exists(os.path.join(root, ".claude-plugin", "marketplace.json")):
        return {"source": "directory", "path": root}
    return None


def write_settings(path, plugin_root):
    settings_file = os.path.join(path, ".claude", "settings.json")
    if os.path.exists(settings_file):
        return f"{settings_file} already exists, left as it is"
    settings = {"enabledPlugins": {f"{PLUGIN}@{MARKETPLACE}": True}}
    source = marketplace_source(plugin_root)
    if source:
        settings = {"extraKnownMarketplaces": {MARKETPLACE: {"source": source}}, **settings}
    os.makedirs(os.path.dirname(settings_file), exist_ok=True)
    with open(settings_file, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2)
        handle.write("\n")
    return f"wrote {settings_file}" + ("" if source else f", without the {MARKETPLACE} marketplace address (not published yet)")


def start_git(path):
    def git(*args):
        return subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)

    try:
        if git("rev-parse", "--is-inside-work-tree").returncode == 0:
            return "already inside a git repository, nothing committed"
        if git("init", "-b", "main").returncode != 0:
            return "git init failed, the vault has no history"
        git("add", "-A")
        if git("commit", "-m", "Start vault").returncode != 0:
            return "git started on main; the first commit failed (set user.name and user.email, then commit)"
    except FileNotFoundError:
        return "git not found, the vault has no history"
    return "git started on main with a first commit"


def init(data_dir, plugin_root, target):
    path = absolute(target)
    report = []
    if is_vault(path):
        report.append(f"{path} is already a vault, registered as it is")
        lacking = missing(path, OPTIONAL)
        if lacking:
            report.append(f"missing {', '.join(lacking)}")
        report.append(write_settings(path, plugin_root))
    else:
        if os.path.exists(path) and (not os.path.isdir(path) or os.listdir(path)):
            fail(f"{path} exists and is not empty. Give a new or empty directory, or the path of an existing vault.")
        template = os.path.join(absolute(plugin_root), "template")
        if not is_vault(template):
            fail(f"{template} is missing: reinstall the {PLUGIN} plugin.")
        shutil.copytree(template, path, dirs_exist_ok=True)
        report.append(f"created the vault at {path}")
        report.append(write_settings(path, plugin_root))
        report.append(start_git(path))
    os.makedirs(data_dir, exist_ok=True)
    with open(os.path.join(data_dir, PATH_FILE), "w", encoding="utf-8") as handle:
        handle.write(path + "\n")
    report.append(f"saved the path in {os.path.join(data_dir, PATH_FILE)}")
    print("\n".join(report))


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "resolve":
        print(resolve(args[1]))
    elif len(args) == 4 and args[0] == "init":
        init(args[1], args[2], args[3])
    else:
        fail(__doc__)


if __name__ == "__main__":
    main()
