#!/usr/bin/env bash
#
# Scales the Celery `worker` and `beat` process groups up before a race
# weekend or back down to 0 afterward. `web` is always-on (see fly.toml's
# module comment) and is never touched by this script.
#
# Everything gated behind worker/beat — the live WebSocket timing tower,
# POST /strategy/simulate, and auto race detection — degrades visibly
# rather than silently when they're at 0 (see web/src/hooks/
# useLiveTelemetry.ts's staleConnection and useStrategy.ts's
# useSimulationResult timedOut). See docs/runbook.md's "Fly.io race
# weekend scaling" section for the full checklist this fits into.
#
# Usage:
#   ./infra/fly/scale-race-weekend.sh up
#   ./infra/fly/scale-race-weekend.sh down
#
# Also wired as `make fly-race-up` / `make fly-race-down`.

set -euo pipefail

DIRECTION="${1:-}"

if [[ "$DIRECTION" != "up" && "$DIRECTION" != "down" ]]; then
  echo "Usage: $0 <up|down>" >&2
  exit 1
fi

if [[ "$DIRECTION" == "up" ]]; then
  fly scale count worker=1 beat=1 --app f1-strategy --yes
  echo ""
  echo "Scaling worker + beat up for race weekend."
  echo "Remember to scale down after the race:"
  echo "  make fly-race-down"
else
  fly scale count worker=0 beat=0 --app f1-strategy --yes
  echo ""
  echo "Scaling worker + beat down after race."
  echo "Cost saving mode active — web still running."
fi
