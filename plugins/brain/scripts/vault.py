#!/usr/bin/env python3
"""Find and register the vault the brain skills work on.

  vault.py resolve <data-dir>      print the vault path: $BRAIN_DIR, else the path file
                                   <data-dir>/vault-path, else the current directory when
                                   it is a vault; exit 1 with a message when none is
  vault.py set <data-dir> <path>   write the path file (what /brain:init does)
  vault.py check <path>            list the parts of the vault skeleton that are missing;
                                   exit 1 when the path is not a vault at all

A directory is a vault when it holds wiki/ and index.md.
"""
import os
import sys

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
        fail(f"{path_file} points at {saved}, which is not a vault any more. Run /brain:init in the right directory.")
    if is_vault(os.getcwd()):
        return os.getcwd()
    fail("No vault configured. Run /brain:init from inside your vault directory, or set BRAIN_DIR.")


def main():
    args = sys.argv[1:]
    if len(args) == 2 and args[0] == "resolve":
        print(resolve(args[1]))
    elif len(args) == 3 and args[0] == "set":
        path = absolute(args[2])
        if not is_vault(path):
            fail(f"{path} is not a vault (it needs wiki/ and index.md).")
        os.makedirs(args[1], exist_ok=True)
        with open(os.path.join(args[1], PATH_FILE), "w", encoding="utf-8") as handle:
            handle.write(path + "\n")
        print(path)
    elif len(args) == 2 and args[0] == "check":
        path = absolute(args[1])
        absent = missing(path, REQUIRED)
        if not os.path.isdir(path) or absent:
            fail(f"{path} is not a vault: missing {', '.join(absent) or 'the directory itself'}.")
        lacking = missing(path, OPTIONAL)
        print(f"{path} is a vault" + (f"; missing {', '.join(lacking)}" if lacking else ""))
    else:
        fail(__doc__)


if __name__ == "__main__":
    main()
