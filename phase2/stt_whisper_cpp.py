"""Local STT via whisper.cpp `whisper-cli` subprocess (design brief §5, Phase 2).

No cloud calls. Requires a built whisper.cpp binary + a ggml model on disk
(see docs/PHASE2.md "Dependencies"). CPU-only; on Protectli's Celeron
N5105 a ~90s clip takes a few minutes (encoder pass dominates, not beam
search — see `transcribe()`), so this must run off the demod/SAME path.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


class SttError(RuntimeError):
    """Transcription failed; caller should log and leave transcript null."""


@dataclass
class TranscriptResult:
    text: str
    confidence: float | None  # mean per-token probability, 0..1; None if unavailable


def transcribe(
    wav_path: Path,
    *,
    model_path: Path,
    binary_path: Path,
    threads: int | None = None,
    language: str = "en",
    beam_size: int = 5,
    best_of: int = 5,
    timeout_s: float = 600.0,
) -> TranscriptResult:
    """Run whisper-cli on a single WAV and return full text + mean token confidence.

    Defaults match whisper.cpp's own (beam_size=5, best_of=5). Tried
    forcing greedy (1/1) on Protectli's Celeron N5105 to cut latency, but
    on a real RWT clip it wasn't meaningfully faster (encoder pass, not
    beam search, dominates wall time here) and it was visibly worse
    quality — it dropped the whole read-out of county names as
    "[static]" where beam search transcribed them correctly. Keep the
    defaults; lower them via config/phase2.env only if a real clip proves
    too slow.

    Raises SttError on any failure (missing binary/model, non-zero exit, bad
    output). Caller is responsible for catching this and not propagating it
    into the demod/SAME path.
    """
    if not Path(binary_path).exists():
        raise SttError(f"whisper.cpp binary not found: {binary_path}")
    if not Path(model_path).exists():
        raise SttError(f"whisper.cpp model not found: {model_path}")
    if not wav_path.exists():
        raise SttError(f"WAV not found: {wav_path}")

    with tempfile.TemporaryDirectory() as tmp:
        out_prefix = Path(tmp) / "out"
        cmd = [
            str(binary_path),
            "-m", str(model_path),
            "-f", str(wav_path),
            "-l", language,
            "-oj", "-ojf",  # JSON with per-token probabilities
            "-of", str(out_prefix),
            "-np",  # only the requested outputs, no progress spam
            "-bs", str(beam_size),
            "-bo", str(best_of),
        ]
        if threads:
            cmd += ["-t", str(threads)]

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_s
            )
        except subprocess.TimeoutExpired as e:
            raise SttError(f"whisper-cli timed out after {timeout_s}s") from e
        except OSError as e:
            raise SttError(f"failed to launch whisper-cli: {e}") from e

        if proc.returncode != 0:
            raise SttError(
                f"whisper-cli exited {proc.returncode}: {proc.stderr.strip()[-500:]}"
            )

        json_path = out_prefix.with_suffix(".json")
        try:
            data = json.loads(json_path.read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise SttError(f"could not read whisper-cli JSON output: {e}") from e

    return _parse_result(data)


def _parse_result(data: dict) -> TranscriptResult:
    segments = data.get("transcription", [])
    texts: list[str] = []
    probs: list[float] = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if text:
            texts.append(text)
        for tok in seg.get("tokens", []):
            p = tok.get("p")
            if p is not None:
                probs.append(float(p))

    full_text = " ".join(texts).strip()
    confidence = (sum(probs) / len(probs)) if probs else None
    return TranscriptResult(text=full_text, confidence=confidence)
