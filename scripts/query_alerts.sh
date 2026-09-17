#!/usr/bin/env bash
# Show recent alerts from the Phase 1 SQLite DB, or search with filters.
# Delegates to phase3/query.py — see docs/PHASE3.md "Minimal UI / query".
#
# Usage:
#   ./scripts/query_alerts.sh                         # last 20 rows (back-compat)
#   ./scripts/query_alerts.sh /path/to/alerts.db       # last 20 rows, different DB
#   ./scripts/query_alerts.sh --event TOR
#   ./scripts/query_alerts.sh --fips 055025
#   ./scripts/query_alerts.sh --since 2026-09-01 --until 2026-09-17
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/phase3/query.py" "$@"
