#!/usr/bin/env python3
"""Quick unit checks for AlertDB Phase 2 methods (no SDR required)."""
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
        finally:
            db.close()

    print("db.py Phase 2 methods OK")


if __name__ == "__main__":
    main()
