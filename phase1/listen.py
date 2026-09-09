#!/usr/bin/env python3
"""Phase 1 NWR listener: rtl_fm -> ring buffer + multimon-ng SAME -> WAV clip + SQLite.

Design brief Phase 1 only (no Whisper). Run on Protectli after Phase 0 smoke
test passes and kernel DVB modules are blacklisted / unloaded.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import threading
import time
import wave
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import AlertDB
from same import SameHeader, is_eom, parse_same_header

log = logging.getLogger("nwr.listen")

# Raw audio from rtl_fm: signed 16-bit LE mono
BYTES_PER_SAMPLE = 2


class RingBuffer:
    def __init__(self, seconds: float, sample_rate: int):
        self.sample_rate = sample_rate
        self.max_bytes = int(seconds * sample_rate) * BYTES_PER_SAMPLE
        self._buf = deque()
        self._size = 0
        self._lock = threading.Lock()

    def write(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            self._buf.append(data)
            self._size += len(data)
            while self._size > self.max_bytes and self._buf:
                old = self._buf.popleft()
                self._size -= len(old)

    def snapshot(self) -> bytes:
        with self._lock:
            return b"".join(self._buf)


class ClipWriter:
    def __init__(self, path: Path, sample_rate: int, pre_roll: bytes):
        self.path = path
        self.sample_rate = sample_rate
        self._fh = path.open("wb")
        self._frames = 0
        if pre_roll:
            self._fh.write(pre_roll)
            self._frames += len(pre_roll) // BYTES_PER_SAMPLE

    def write(self, data: bytes) -> None:
        if not data:
            return
        self._fh.write(data)
        self._frames += len(data) // BYTES_PER_SAMPLE

    def close_to_wav(self, wav_path: Path) -> Path:
        self._fh.close()
        raw = self.path.read_bytes()
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(raw)
        self.path.unlink(missing_ok=True)
        return wav_path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def build_rtl_cmd(args: argparse.Namespace) -> list[str]:
    cmd = [
        "rtl_fm",
        "-d",
        str(args.device),
        "-f",
        str(args.freq_hz),
        "-M",
        "fm",
        "-s",
        str(args.sample_rate),
        "-A",
        "fast",
        "-l",
        "0",
        "-E",
        "deemp",
    ]
    if args.ppm:
        cmd += ["-p", str(args.ppm)]
    if args.gain != 0:
        cmd += ["-g", str(args.gain)]
    if args.sudo_rtl:
        cmd = ["sudo", "-n", *cmd]
    return cmd


def matches_fips_filter(header: SameHeader, required: list[str]) -> bool:
    if not required:
        return True
    return any(f in header.fips_list for f in required)


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    load_dotenv(root / "config" / "phase1.env")
    load_dotenv(root / "config" / "smoke.env")  # optional shared device settings

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--freq-hz", type=int, default=int(os.environ.get("FREQ_HZ", "162550000")))
    p.add_argument("--device", default=os.environ.get("DEVICE_INDEX", "0"))
    p.add_argument("--sample-rate", type=int, default=int(os.environ.get("SAMPLE_RATE", "22050")))
    p.add_argument("--gain", type=float, default=float(os.environ.get("GAIN", "0")))
    p.add_argument("--ppm", type=int, default=int(os.environ.get("PPM", "0")))
    p.add_argument("--sudo-rtl", action="store_true", default=os.environ.get("USE_SUDO_RTL", "0") == "1")
    p.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", str(root / "data"))))
    p.add_argument("--pre-roll-s", type=float, default=float(os.environ.get("PRE_ROLL_S", "3")))
    p.add_argument("--max-clip-s", type=float, default=float(os.environ.get("MAX_CLIP_S", "600")))
    p.add_argument(
        "--require-fips",
        default=os.environ.get("REQUIRE_COUNTY_FIPS", "055025"),
        help="Comma-separated FIPS list; empty = store all",
    )
    p.add_argument("--store-tests", action="store_true", default=os.environ.get("STORE_TESTS", "1") == "1")
    p.add_argument("--dry-run", action="store_true", help="Log SAME only; no DB/WAV writes")
    args = p.parse_args(argv)

    required_fips = [x.strip() for x in args.require_fips.split(",") if x.strip()]
    freq_mhz = args.freq_hz / 1_000_000.0

    audio_dir = args.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    db = None if args.dry_run else AlertDB(args.data_dir / "alerts.db")

    ring = RingBuffer(args.pre_roll_s, args.sample_rate)
    stop = threading.Event()

    state_lock = threading.Lock()
    active_clip: ClipWriter | None = None
    active_headers: list[SameHeader] = []
    clip_deadline = 0.0
    pending_alert_id: int | None = None

    def close_clip(reason: str) -> None:
        nonlocal active_clip, active_headers, pending_alert_id
        with state_lock:
            clip = active_clip
            headers = list(active_headers)
            alert_id = pending_alert_id
            active_clip = None
            active_headers = []
            pending_alert_id = None
        if clip is None:
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        event = headers[0].event if headers else "UNK"
        wav_path = audio_dir / f"{ts}-{event}.wav"
        try:
            clip.close_to_wav(wav_path)
        except Exception:
            log.exception("failed to finalize clip (%s)", reason)
            return
        log.info("clip closed (%s): %s (%d headers)", reason, wav_path, len(headers))
        if db is not None and alert_id is not None:
            db.update_audio_path(alert_id, str(wav_path))

    def on_same_line(line: str) -> None:
        nonlocal active_clip, active_headers, clip_deadline, pending_alert_id
        line = line.strip()
        if not line:
            return
        log.info("multimon: %s", line)

        if is_eom(line):
            close_clip("EOM/NNNN")
            return

        header = parse_same_header(line)
        if header is None:
            return

        if not matches_fips_filter(header, required_fips):
            log.info("skip header (FIPS filter %s): %s", required_fips, header.fips_list)
            return
        if header.is_test and not args.store_tests:
            log.info("skip test event %s (STORE_TESTS=0)", header.event)
            return

        with state_lock:
            if active_clip is None:
                if args.dry_run:
                    log.info("DRY-RUN would start clip for %s %s", header.event, header.raw)
                    return
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                raw_path = audio_dir / f".partial-{ts}.raw"
                pre = ring.snapshot()
                active_clip = ClipWriter(raw_path, args.sample_rate, pre)
                active_headers = [header]
                clip_deadline = time.monotonic() + args.max_clip_s
                result = db.insert(header, freq_mhz, audio_path=None) if db else None
                if result and result.duplicate:
                    log.info("duplicate SAME (dedup_key=%s, id=%s) — still recording clip once", result.dedup_key, result.alert_id)
                    pending_alert_id = result.alert_id
                elif result:
                    pending_alert_id = result.alert_id
                    log.info("DB alert id=%s event=%s fips=%s", result.alert_id, header.event, header.fips_list)
                else:
                    pending_alert_id = None
            else:
                # Additional identical/near headers during the same alert burst
                active_headers.append(header)

    def read_multimon(proc: subprocess.Popen) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            if stop.is_set():
                break
            try:
                on_same_line(line)
            except Exception:
                log.exception("SAME handler error")

    rtl_cmd = build_rtl_cmd(args)
    mm_cmd = ["multimon-ng", "-a", "SAME", "-t", "raw", "/dev/stdin"]
    log.info("rtl: %s", " ".join(rtl_cmd))
    log.info("multimon: %s", " ".join(mm_cmd))
    log.info("freq=%.3f MHz data_dir=%s fips_filter=%s", freq_mhz, args.data_dir, required_fips or "(all)")

    rtl = subprocess.Popen(rtl_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    mm = subprocess.Popen(
        mm_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def log_rtl_stderr() -> None:
        assert rtl.stderr is not None
        for line in rtl.stderr:
            log.warning("rtl_fm: %s", line.rstrip())

    threading.Thread(target=read_multimon, args=(mm,), daemon=True).start()
    threading.Thread(target=log_rtl_stderr, daemon=True).start()

    def handle_sig(_signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGINT, handle_sig)
    signal.signal(signal.SIGTERM, handle_sig)

    assert rtl.stdout is not None and mm.stdin is not None
    chunk = args.sample_rate * BYTES_PER_SAMPLE // 10  # 100 ms
    try:
        while not stop.is_set():
            data = rtl.stdout.read(chunk)
            if not data:
                log.error("rtl_fm stdout EOF — device lost or claim failed")
                break
            ring.write(data)
            try:
                mm.stdin.write(data)
                mm.stdin.flush()
            except BrokenPipeError:
                log.error("multimon-ng stdin closed")
                break
            with state_lock:
                clip = active_clip
                deadline = clip_deadline
            if clip is not None:
                clip.write(data)
                if time.monotonic() >= deadline:
                    close_clip("max_clip_s timeout")
    finally:
        stop.set()
        close_clip("shutdown")
        for proc in (mm, rtl):
            try:
                proc.terminate()
            except Exception:
                pass
        if db is not None:
            db.close()
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    raise SystemExit(main())
