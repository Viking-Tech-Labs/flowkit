#!/usr/bin/env bash
# Keep the computer awake while a turn runs, and between turns while the session is reachable
# from a phone (Remote Control) or the user is away. A no-op where `caffeinate` (macOS) is absent.
#
# Why a second assertion although Claude Code has its own sleep inhibitor: the built-in one is a
# `caffeinate -i -t 300` child that is killed and respawned every few minutes, kill first. With the
# display already off, that gap of about a second is enough for the system to commit to idle sleep.
# This hook keeps an overlapping assertion alive so the count never drops to zero.
#
# Wired in hooks/hooks.json:
#   UserPromptSubmit, PreToolUse -> keep-awake.sh hold
#   Stop                         -> keep-awake.sh idle-hold
#   SessionEnd                   -> keep-awake.sh release
#
# hold:      if this session has no turn hold younger than RENEW_MIN, start one (capped at
#            MAX_MIN, bound to the Claude process with -w so it dies with the session). Older
#            ones expire on their own, so renewals overlap instead of leaving a gap.
# idle-hold: end of turn. The turn hold is released; first, when Remote Control is attached or
#            the away flag is set, an idle hold of IDLE_MIN takes over, so the session stays
#            answerable and a chain of sessions keeps going between turns.
# release:   stop every hold this hook started for this session.
#
# -i only: the display still sleeps. A CLOSED LID STILL SLEEPS a laptop, which nothing here can
# change: leave the lid open and the power connected.

MAX_MIN=15
RENEW_MIN=5
IDLE_MIN=120
AWAY_MIN=720
AWAY="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/away"

cat >/dev/null   # drain the hook's stdin JSON; everything needed is in the environment

command -v caffeinate >/dev/null 2>&1 || exit 0
[ -n "$CLAUDE_PID" ] || exit 0

# Our processes are recognisable by their exact argv; the built-in one has no -w.
# The two roles differ only in -t, which is enough for pgrep/pkill -x to tell them apart.
turn_tag="caffeinate -i -t $((MAX_MIN * 60)) -w $CLAUDE_PID"
idle_tag="caffeinate -i -t $((IDLE_MIN * 60)) -w $CLAUDE_PID"

# Age in seconds of the youngest process matching $1. ps etime is [[D-]HH:]MM:SS.
youngest_age() {
  local best='' p e s
  for p in $(pgrep -f -x "$1" 2>/dev/null); do
    e=$(ps -o etime= -p "$p" 2>/dev/null | tr -d ' ')
    [ -n "$e" ] || continue
    s=$(printf '%s' "$e" | awk -F'[-:]' '{ n=NF; sec=$n + $(n-1)*60; if (n>=3) sec += $(n-2)*3600; if (n==4) sec += $1*86400; print sec }')
    if [ -z "$best" ] || [ "$s" -lt "$best" ]; then best=$s; fi
  done
  printf '%s' "$best"
}

# The away flag counts while it is younger than AWAY_MIN; find -mmin works on macOS and Linux.
away() {
  [ -f "$AWAY" ] && [ -n "$(find "$AWAY" -mmin -"$AWAY_MIN" 2>/dev/null)" ]
}

case "$1" in
  hold)
    age=$(youngest_age "$turn_tag")
    if [ -n "$age" ] && [ "$age" -lt $((RENEW_MIN * 60)) ]; then exit 0; fi
    nohup $turn_tag </dev/null >/dev/null 2>&1 &
    disown
    ;;
  idle-hold)
    # CLAUDE_CODE_BRIDGE_SESSION_ID is set while Remote Control is attached. Start the
    # replacement BEFORE releasing the turn hold, or this reopens the gap described above.
    if [ -n "$CLAUDE_CODE_BRIDGE_SESSION_ID" ] || away; then
      pkill -f -x "$idle_tag" 2>/dev/null || true   # restart the window on each turn
      nohup $idle_tag </dev/null >/dev/null 2>&1 &
      disown
    fi
    pkill -f -x "$turn_tag" 2>/dev/null || true
    ;;
  release)
    pkill -f -x "$turn_tag" 2>/dev/null || true
    pkill -f -x "$idle_tag" 2>/dev/null || true
    ;;
esac
exit 0
