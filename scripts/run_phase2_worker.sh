#!/usr/bin/env bash
# Start the Phase 2 STT poller on Protectli. Separate process from
# nwr-alerts.service (Phase 1) by design — see phase2/worker.py docstring.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f config/phase2.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source config/phase2.env
  set +a
elif [[ -f config/phase2.env.example ]]; then
  echo "NOTE: using config/phase2.env.example (copy to phase2.env to customize)"
  set -a
  # shellcheck disable=SC1091
  source config/phase2.env.example
  set +a
fi

if [[ "${STT_ENABLED:-0}" != "1" ]]; then
  echo "STT_ENABLED != 1 in config/phase2.env — nothing to do." >&2
  exit 0
fi

need() { command -v "$1" >/dev/null || { echo "missing $1" >&2; exit 1; }; }
need python3

exec python3 "$ROOT/phase2/worker.py" "$@"
