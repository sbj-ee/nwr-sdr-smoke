#!/usr/bin/env python3
"""Quick unit checks for AlertDB Phase 2/3 methods (no SDR required)."""
import tempfile
from pathlib import Path

from db import AlertDB
from same import parse_same_header


def main() -> None:
    header = parse_same_header(
        "EAS: ZCZC-WXR-RWT-055025+0030-2609091200-ABC12345-"
    )
    assert header is not None

    with tempfile.TemporaryDirectory() as tmp:
        db = AlertDB(Path(tmp) / "alerts.db")
        try:
            result = db.insert(header, 162.550, audio_path=None)
            assert not result.duplicate
            alert_id = result.alert_id

            assert db.find_pending_transcripts() == []

            db.update_audio_path(alert_id, "/tmp/clip.wav")
            pending = db.find_pending_transcripts()
            assert len(pending) == 1
            assert pending[0]["id"] == alert_id
            assert pending[0]["audio_path"] == "/tmp/clip.wav"

            db.update_transcript(alert_id, "test transcript", 0.87)
            assert db.find_pending_transcripts() == []

            row = db._conn.execute(
                "SELECT transcript, transcript_conf FROM alerts WHERE id = ?", (alert_id,)
            ).fetchone()
            assert row["transcript"] == "test transcript"
            assert abs(row["transcript_conf"] - 0.87) < 1e-9

            # Re-run overwrites, not appends.
            db.update_transcript(alert_id, "second pass", None)
            row = db._conn.execute(
                "SELECT transcript, transcript_conf FROM alerts WHERE id = ?", (alert_id,)
            ).fetchone()
            assert row["transcript"] == "second pass"
            assert row["transcript_conf"] is None

            # --- Phase 3 ---
            pending = db.find_pending_notifications()
            assert len(pending) == 1
            assert pending[0]["id"] == alert_id

            db.mark_notified(alert_id, "house-nwr,house-urgent")
            assert db.find_pending_notifications() == []
            row = db._conn.execute(
                "SELECT notified_at, notify_topic FROM alerts WHERE id = ?", (alert_id,)
            ).fetchone()
            assert row["notified_at"] is not None
            assert row["notify_topic"] == "house-nwr,house-urgent"

            counts = db.counts_since("2000-01-01T00:00:00+00:00")
            assert len(counts) == 1
            assert counts[0]["event"] == "RWT"
            assert counts[0]["n"] == 1
        finally:
            db.close()

        # Re-opening the same DB file must not error on the Phase 3 migration
        # (ALTER TABLE ADD COLUMN on columns that already exist).
        db2 = AlertDB(Path(tmp) / "alerts.db")
        db2.close()

    print("db.py Phase 2/3 methods OK")


if __name__ == "__main__":
    main()
