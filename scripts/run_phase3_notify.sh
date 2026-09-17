#!/usr/bin/env bash
# Start the Phase 3 notify poller on Protectli. Separate process from
# nwr-alerts.service (Phase 1) and nwr-phase2-worker.service — see
# phase3/notify_worker.py docstring.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f config/phase3.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source config/phase3.env
  set +a
elif [[ -f config/phase3.env.example ]]; then
  echo "NOTE: using config/phase3.env.example (copy to phase3.env to customize)"
  set -a
  # shellcheck disable=SC1091
  source config/phase3.env.example
  set +a
fi

if [[ "${NOTIFY_ENABLED:-0}" != "1" ]]; then
  echo "NOTIFY_ENABLED != 1 in config/phase3.env — nothing to do." >&2
  exit 0
fi

need() { command -v "$1" >/dev/null || { echo "missing $1" >&2; exit 1; }; }
need python3

exec python3 "$ROOT/phase3/notify_worker.py" "$@"
