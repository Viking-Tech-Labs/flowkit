#!/bin/sh
# Runs the unit tests of every plugin that has a tests/ folder, then the maintainer scripts' own:
# the Python suites with unittest, the board's TypeScript with `claude plugin test`. CI runs this,
# and scripts/release.py refuses to release unless it exits 0.
# Usage: scripts/test.sh [name ...]   (a plugin's name, or `scripts`; default: all of them)
cd "$(dirname "$0")/.." || exit 1

names=$*
[ -n "$names" ] || names="$(for d in plugins/*/tests; do basename "$(dirname "$d")"; done) scripts"

red=""
for name in $names; do
  if [ "$name" = scripts ]; then
    dir=scripts
  else
    dir=plugins/$name
  fi
  if [ ! -d "$dir/tests" ]; then
    echo "test.sh: no tests in $dir" >&2
    exit 2
  fi
  set -- "$dir"/tests/*.test.ts*
  if [ -e "$1" ]; then
    echo "== $name: claude plugin test $dir"
    claude plugin test "$dir" || red="$red $name"
  else
    echo "== $name: python3 -m unittest discover $dir/tests"
    python3 -m unittest discover "$dir/tests" || red="$red $name"
  fi
done

if [ -n "$red" ]; then
  echo "red:$red"
  exit 1
fi
echo "green"
