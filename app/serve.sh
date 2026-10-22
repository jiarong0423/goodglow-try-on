#!/bin/sh
# Container entry point. Live mode for signed-in judges needs all four settings and a password of at least 10
# characters; anything less starts the safe default (demo mode, no YouCam calls, no sign-in) and says why.
# The values themselves are never printed, only which setting is missing or too short.
PORT="${PORT:-8787}"
MAX_CALLS="${GOODGLOW_MAX_CALLS:-5}"
BUDGET_CAP="${GOODGLOW_BUDGET_CAP:-30}"
live=1
missing=""
[ "$GOODGLOW_LIVE" = "1" ] || { live=0; missing="$missing GOODGLOW_LIVE=1"; }
[ -n "$YOUCAM_API_KEY" ] || { live=0; missing="$missing YOUCAM_API_KEY"; }
[ -n "$GOODGLOW_JUDGE_USER" ] || { live=0; missing="$missing GOODGLOW_JUDGE_USER"; }
[ -n "$GOODGLOW_JUDGE_PASSWORD" ] || { live=0; missing="$missing GOODGLOW_JUDGE_PASSWORD"; }
if [ "$live" = "1" ] && [ "${#GOODGLOW_JUDGE_PASSWORD}" -lt 10 ]; then
  live=0
  missing="$missing GOODGLOW_JUDGE_PASSWORD(shorter than 10 characters)"
fi
if [ "$live" = "1" ]; then
  echo "serve: judge live mode enabled (max $MAX_CALLS calls per start, budget cap $BUDGET_CAP)"
  exec python -B app/server.py --judge-live --catalog demo --host 0.0.0.0 --port "$PORT" --max-calls "$MAX_CALLS" --budget-cap "$BUDGET_CAP"
fi
if [ "$GOODGLOW_LIVE" = "1" ]; then
  echo "WARNING serve: GOODGLOW_LIVE=1 but live mode stays off; missing or invalid:$missing. Starting demo mode." >&2
fi
exec python -B app/server.py --mock --catalog demo --host 0.0.0.0 --port "$PORT"
