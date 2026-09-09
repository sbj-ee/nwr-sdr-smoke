#!/usr/bin/env bash
# Show recent alerts from the Phase 1 SQLite DB.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB="${1:-$ROOT/data/alerts.db}"
if [[ ! -f "$DB" ]]; then
  echo "no DB at $DB — run Phase 1 first" >&2
  exit 1
fi
sqlite3 -header -column "$DB" \
  "SELECT id, received_at, event, event_label, fips_list, audio_path, substr(same_header_raw,1,60) AS hdr
   FROM alerts ORDER BY id DESC LIMIT 20;"
