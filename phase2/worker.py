#!/usr/bin/env python3
"""Phase 2 STT worker: standalone poller, isolated from phase1/listen.py.

Architecture choice (docs/PHASE2.md "Implementation options", #2 — side
worker): this runs as its own process, polling AlertDB for rows that have
a finalized clip (`audio_path` set) but no `transcript` yet, transcribing
them, and writing the result back. It never touches the rtl_fm/multimon-ng
read loop or demod/SAME threads in listen.py, so a stuck or crashing STT
run cannot starve audio capture or take down `nwr-alerts.service`. Enable
independently via `scripts/run_phase2_worker.sh`.

Usage:
    python3 phase2/worker.py                    # poll loop (STT_ENABLED must be 1)
    python3 phase2/worker.py --once              # single pass over pending rows, then exit
    python3 phase2/worker.py --file clip.wav      # offline dry-run: print text + confidence
    python3 phase2/worker.py --file clip.wav --alert-id 7   # offline: also write DB row 7
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "phase1"))

from db import AlertDB  # phase1/db.py — no package prefix, matches phase1/listen.py's own import style
from stt_whisper_cpp import SttError, TranscriptResult, transcribe

log = logging.getLogger("nwr.phase2.worker")


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def run_one(
    wav_path: Path,
    *,
    model_path: Path,
    binary_path: Path,
    threads: int | None,
    language: str,
    beam_size: int = 5,
    best_of: int = 5,
) -> TranscriptResult:
    return transcribe(
        wav_path,
        model_path=model_path,
        binary_path=binary_path,
        threads=threads,
        language=language,
        beam_size=beam_size,
        best_of=best_of,
    )


def poll_once(
    db: AlertDB,
    *,
    model_path: Path,
    binary_path: Path,
    threads: int | None,
    language: str,
    beam_size: int,
    best_of: int,
    limit: int,
) -> int:
    """One pass over pending rows. Returns count processed (success or failure)."""
    pending = db.find_pending_transcripts(limit=limit)
    for row in pending:
        alert_id = int(row["id"])
        wav_path = Path(row["audio_path"])
        try:
            result = run_one(
                wav_path,
                model_path=model_path,
                binary_path=binary_path,
                threads=threads,
                language=language,
                beam_size=beam_size,
                best_of=best_of,
            )
        except SttError as e:
            # Exit criterion 3: STT failures log and leave transcript null; never crash.
            log.error("STT failed for alert id=%s (%s): %s", alert_id, wav_path, e)
            continue
        db.update_transcript(alert_id, result.text, result.confidence)
        log.info(
            "transcribed alert id=%s conf=%s chars=%d",
            alert_id,
            f"{result.confidence:.3f}" if result.confidence is not None else "n/a",
            len(result.text),
        )
    return len(pending)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / "config" / "phase1.env")
    load_dotenv(root / "config" / "phase2.env")

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", str(root / "data"))))
    p.add_argument("--model-path", type=Path, default=Path(os.environ.get("STT_MODEL_PATH", "data/models/ggml-base.en.bin")))
    p.add_argument("--binary-path", type=Path, default=Path(os.environ.get("WHISPER_CPP_BIN", "whisper-cli")))
    p.add_argument("--threads", type=int, default=int(os.environ.get("STT_THREADS", "0")) or None)
    p.add_argument("--language", default=os.environ.get("STT_LANGUAGE", "en"))
    p.add_argument("--beam-size", type=int, default=int(os.environ.get("STT_BEAM_SIZE", "5")))
    p.add_argument("--best-of", type=int, default=int(os.environ.get("STT_BEST_OF", "5")))
    p.add_argument("--interval-s", type=float, default=float(os.environ.get("STT_POLL_INTERVAL_S", "15")))
    p.add_argument("--limit", type=int, default=int(os.environ.get("STT_POLL_LIMIT", "5")))
    p.add_argument("--once", action="store_true", help="Single pass over pending rows, then exit")
    p.add_argument("--file", type=Path, help="Offline: transcribe this WAV directly (bypasses DB polling)")
    p.add_argument("--alert-id", type=int, help="With --file: also write the result to this alert's DB row")
    args = p.parse_args(argv)

    if args.file:
        result = run_one(
            args.file,
            model_path=args.model_path,
            binary_path=args.binary_path,
            threads=args.threads,
            language=args.language,
            beam_size=args.beam_size,
            best_of=args.best_of,
        )
        print(f"confidence: {result.confidence}")
        print(f"text: {result.text}")
        if args.alert_id is not None:
            db = AlertDB(args.data_dir / "alerts.db")
            db.update_transcript(args.alert_id, result.text, result.confidence)
            db.close()
            log.info("wrote transcript to alert id=%s", args.alert_id)
        return 0

    if os.environ.get("STT_ENABLED", "0") != "1" and not args.once:
        log.warning("STT_ENABLED != 1 — exiting without polling (use --once to force a single pass)")
        return 0

    db = AlertDB(args.data_dir / "alerts.db")
    try:
        if args.once:
            n = poll_once(
                db,
                model_path=args.model_path,
                binary_path=args.binary_path,
                threads=args.threads,
                language=args.language,
                beam_size=args.beam_size,
                best_of=args.best_of,
                limit=args.limit,
            )
            log.info("processed %d pending row(s)", n)
            return 0

        log.info("phase2 worker polling every %.0fs (limit=%d/pass)", args.interval_s, args.limit)
        while True:
            try:
                poll_once(
                    db,
                    model_path=args.model_path,
                    binary_path=args.binary_path,
                    threads=args.threads,
                    language=args.language,
                    beam_size=args.beam_size,
                    best_of=args.best_of,
                    limit=args.limit,
                )
            except Exception:
                log.exception("poll pass failed; will retry next interval")
            time.sleep(args.interval_s)
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(main())
