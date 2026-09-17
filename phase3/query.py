#!/usr/bin/env python3
"""Phase 3 query CLI: search stored alerts by event / date range / FIPS.

Replaces the fixed "last 20 rows" sqlite3 one-liner scripts/query_alerts.sh
used to run directly; that script now delegates here so existing usage
(`./scripts/query_alerts.sh`) keeps working unfiltered, while flags add
the search docs/PHASE3.md asks for.

Examples:
    python3 phase3/query.py
    python3 phase3/query.py --event TOR
    python3 phase3/query.py --fips 055025
    python3 phase3/query.py --since 2026-09-01 --until 2026-09-17
    python3 phase3/query.py --event SVR --fips 055025 --since 2026-09-01
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def build_query(args: argparse.Namespace) -> tuple[str, list]:
    where: list[str] = []
    params: list = []

    if args.event:
        where.append("event = ?")
        params.append(args.event.upper())
    if args.fips:
        where.append("fips_list LIKE ?")
        params.append(f'%"{args.fips}"%')
    if args.since:
        where.append("received_at >= ?")
        params.append(f"{args.since}T00:00:00+00:00")
    if args.until:
        where.append("received_at <= ?")
        params.append(f"{args.until}T23:59:59+00:00")

    sql = """
        SELECT id, received_at, event, event_label, severity, fips_list,
               notified_at, notify_topic, substr(transcript, 1, 60) AS transcript_preview
        FROM alerts
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(args.limit)
    return sql, params


def format_rows(rows: list[sqlite3.Row]) -> str:
    if not rows:
        return "(no matching rows)"
    cols = ["id", "received_at", "event", "severity", "fips", "notified_at", "notify_topic", "transcript"]
    out_rows = []
    for r in rows:
        try:
            fips = ",".join(json.loads(r["fips_list"]) or [])
        except (TypeError, json.JSONDecodeError):
            fips = r["fips_list"] or ""
        out_rows.append(
            [
                str(r["id"]),
                r["received_at"] or "",
                r["event"] or "",
                r["severity"] or "other",
                fips,
                r["notified_at"] or "",
                r["notify_topic"] or "",
                r["transcript_preview"] or "",
            ]
        )
    widths = [max(len(cols[i]), *(len(row[i]) for row in out_rows)) for i in range(len(cols))]
    lines = ["  ".join(c.ljust(w) for c, w in zip(cols, widths))]
    lines.append("  ".join("-" * w for w in widths))
    for row in out_rows:
        lines.append("  ".join(c.ljust(w) for c, w in zip(row, widths)))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("db", nargs="?", default=None, help="Path to alerts.db (back-compat positional; default data/alerts.db)")
    p.add_argument("--db", dest="db_flag", default=None, help="Path to alerts.db")
    p.add_argument("--event", help="Exact SAME event code, e.g. TOR, SVR, RWT")
    p.add_argument("--fips", help="6-digit FIPS code; matches rows whose fips_list contains it")
    p.add_argument("--since", help="YYYY-MM-DD, inclusive")
    p.add_argument("--until", help="YYYY-MM-DD, inclusive")
    p.add_argument("--limit", type=int, default=20)
    args = p.parse_args(argv)

    db_path = Path(args.db_flag or args.db or (root / "data" / "alerts.db"))
    if not db_path.exists():
        print(f"no DB at {db_path} — run Phase 1 first", file=sys.stderr)
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    sql, params = build_query(args)
    rows = conn.execute(sql, params).fetchall()
    conn.close()

    print(format_rows(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
