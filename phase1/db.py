"""SQLite alert store (design brief §6), Phase 1."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from same import SameHeader

SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  received_at TEXT NOT NULL,
  same_header_raw TEXT NOT NULL,
  event TEXT,
  event_label TEXT,
  severity TEXT,
  urgency TEXT,
  certainty TEXT,
  fips_list TEXT NOT NULL,
  transmitter_freq REAL,
  purge_minutes INTEGER,
  originator TEXT,
  audio_path TEXT,
  transcript TEXT,
  transcript_conf REAL,
  source TEXT NOT NULL DEFAULT 'nwr-sdr',
  dedup_key TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_alerts_received_at ON alerts(received_at);
CREATE INDEX IF NOT EXISTS idx_alerts_event ON alerts(event);
CREATE INDEX IF NOT EXISTS idx_alerts_dedup ON alerts(dedup_key);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def dedup_key(header: SameHeader, freq_mhz: float) -> str:
    payload = "|".join(
        [
            header.event,
            ",".join(sorted(header.fips_list)),
            header.issue_time or "",
            f"{freq_mhz:.3f}",
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


@dataclass
class InsertResult:
    alert_id: int | None
    duplicate: bool
    dedup_key: str


class AlertDB:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def find_recent_dedup(self, key: str, within_minutes: int) -> int | None:
        row = self._conn.execute(
            """
            SELECT id FROM alerts
            WHERE dedup_key = ?
              AND received_at >= datetime('now', ?)
            ORDER BY id DESC LIMIT 1
            """,
            (key, f"-{within_minutes} minutes"),
        ).fetchone()
        return int(row["id"]) if row else None

    def insert(
        self,
        header: SameHeader,
        freq_mhz: float,
        audio_path: str | None,
        *,
        within_purge: bool = True,
    ) -> InsertResult:
        key = dedup_key(header, freq_mhz)
        window = header.purge_minutes or 60
        if within_purge:
            existing = self.find_recent_dedup(key, window)
            if existing is not None:
                return InsertResult(alert_id=existing, duplicate=True, dedup_key=key)

        severity = header.event_class if header.event_class in {"warning", "watch", "advisory", "test"} else None
        cur = self._conn.execute(
            """
            INSERT INTO alerts (
              received_at, same_header_raw, event, event_label, severity,
              fips_list, transmitter_freq, purge_minutes, originator,
              audio_path, source, dedup_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'nwr-sdr', ?)
            """,
            (
                utc_now(),
                header.raw,
                header.event,
                header.event_label,
                severity,
                json.dumps(header.fips_list),
                freq_mhz,
                header.purge_minutes,
                header.originator,
                audio_path,
                key,
            ),
        )
        self._conn.commit()
        return InsertResult(alert_id=int(cur.lastrowid), duplicate=False, dedup_key=key)

    def update_audio_path(self, alert_id: int, audio_path: str) -> None:
        self._conn.execute("UPDATE alerts SET audio_path = ? WHERE id = ?", (audio_path, alert_id))
        self._conn.commit()
