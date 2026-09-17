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
  notified_at TEXT,
  notify_topic TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_alerts_received_at ON alerts(received_at);
CREATE INDEX IF NOT EXISTS idx_alerts_event ON alerts(event);
CREATE INDEX IF NOT EXISTS idx_alerts_dedup ON alerts(dedup_key);
"""

# Columns added after the original SCHEMA shipped (Phase 3). New DBs get
# them from CREATE TABLE above; existing Protectli DBs get them here via
# idempotent ALTER TABLE (SQLite has no "ADD COLUMN IF NOT EXISTS" on the
# 3.45 shipped with Ubuntu 24.04, so we catch "duplicate column").
_MIGRATIONS = [
    "ALTER TABLE alerts ADD COLUMN notified_at TEXT",
    "ALTER TABLE alerts ADD COLUMN notify_topic TEXT",
]


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
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()
        for stmt in _MIGRATIONS:
            try:
                self._conn.execute(stmt)
            except sqlite3.OperationalError as e:
                if "duplicate column" not in str(e).lower():
                    raise
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

    def update_transcript(
        self,
        alert_id: int,
        transcript: str,
        transcript_conf: float | None,
    ) -> None:
        """Phase 2. Overwrites any prior transcript for this row (re-run = replace, not append)."""
        self._conn.execute(
            "UPDATE alerts SET transcript = ?, transcript_conf = ? WHERE id = ?",
            (transcript, transcript_conf, alert_id),
        )
        self._conn.commit()

    def find_pending_transcripts(self, limit: int = 20) -> list[sqlite3.Row]:
        """Phase 2. Rows with a finalized clip that still need STT."""
        return self._conn.execute(
            """
            SELECT id, audio_path FROM alerts
            WHERE audio_path IS NOT NULL AND transcript IS NULL
            ORDER BY id ASC LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def find_pending_notifications(self, limit: int = 20) -> list[sqlite3.Row]:
        """Phase 3. Rows not yet through the notify decision (sent or suppressed).

        Every row here is already a first-time insert — Phase 1's dedup
        (`find_recent_dedup`) never creates a second row for a repeat
        broadcast within the purge window, so there's no separate
        "duplicate" flag to check here.
        """
        return self._conn.execute(
            "SELECT * FROM alerts WHERE notified_at IS NULL ORDER BY id ASC LIMIT ?",
            (limit,),
        ).fetchall()

    def counts_since(self, since_iso: str) -> list[sqlite3.Row]:
        """Phase 3 digest. Event counts for rows received at/after `since_iso`."""
        return self._conn.execute(
            """
            SELECT event, event_label, severity, count(*) AS n
            FROM alerts WHERE received_at >= ?
            GROUP BY event ORDER BY n DESC
            """,
            (since_iso,),
        ).fetchall()

    def mark_notified(self, alert_id: int, topic: str) -> None:
        """Phase 3. `topic` is a free-text record of what happened: the actual
        topic(s) published to, or a `suppressed:<reason>` marker — either way
        this stops the row from being re-evaluated on the next poll."""
        self._conn.execute(
            "UPDATE alerts SET notified_at = ?, notify_topic = ? WHERE id = ?",
            (utc_now(), topic, alert_id),
        )
        self._conn.commit()
