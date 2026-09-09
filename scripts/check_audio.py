#!/usr/bin/env python3
"""Score a mono PCM WAV for dead air vs live NWR carrier/voice.

Reads 16-bit WAV (what sox writes from rtl_fm). Prints RMS and peak in
dBFS and exits 0 if both clear the configured floors, else 1.
"""
from __future__ import annotations

import argparse
import math
import struct
import sys
import wave
from pathlib import Path


def db_from_rms(rms: float) -> float:
    if rms <= 0.0:
        return -120.0
    return 20.0 * math.log10(rms)


def analyze(path: Path) -> tuple[float, float, int, int]:
    with wave.open(str(path), "rb") as wf:
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        rate = wf.getframerate()
        nframes = wf.getnframes()
        if sw != 2:
            raise SystemExit(f"{path}: expected 16-bit PCM, got sample width {sw}")
        raw = wf.readframes(nframes)

    # Use first channel only (rtl_fm + sox produce mono).
    fmt = "<" + "h" * (len(raw) // 2)
    samples = struct.unpack(fmt, raw)
    if nch > 1:
        samples = samples[0::nch]

    if not samples:
        raise SystemExit(f"{path}: empty WAV")

    peak = max(abs(s) for s in samples) / 32768.0
    mean_sq = sum((s / 32768.0) ** 2 for s in samples) / len(samples)
    rms = math.sqrt(mean_sq)
    return db_from_rms(rms), db_from_rms(peak), rate, len(samples)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("wav", type=Path)
    p.add_argument("--min-rms-dbfs", type=float, default=-50.0)
    p.add_argument("--min-peak-dbfs", type=float, default=-25.0)
    args = p.parse_args()

    if not args.wav.is_file():
        print(f"FAIL: missing file {args.wav}", file=sys.stderr)
        return 1

    rms_db, peak_db, rate, n = analyze(args.wav)
    dur_s = n / float(rate) if rate else 0.0
    print(f"file={args.wav}")
    print(f"duration_s={dur_s:.2f} rate={rate} samples={n}")
    print(f"rms_dbfs={rms_db:.1f} peak_dbfs={peak_db:.1f}")
    print(f"thresholds: min_rms={args.min_rms_dbfs} min_peak={args.min_peak_dbfs}")

    ok = True
    if rms_db < args.min_rms_dbfs:
        print(f"FAIL: RMS {rms_db:.1f} dBFS below floor {args.min_rms_dbfs}")
        ok = False
    if peak_db < args.min_peak_dbfs:
        print(f"FAIL: peak {peak_db:.1f} dBFS below floor {args.min_peak_dbfs}")
        ok = False
    if ok:
        print("PASS: audio energy looks like live RF, not dead air")
        return 0
    print("HINT: raise GAIN, check antenna, confirm FREQ_HZ=162550000, or unload kernel SDR modules")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
