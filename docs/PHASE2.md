# Phase 2 — Local Whisper transcript (implementer spec)

**Status:** specification only. No STT worker ships in this tree yet.

**Design brief:** [NOAA SDR Weather Alert Pipeline Design v2](https://drive.google.com/file/d/16R4MZVRLa44BPlTqiuuWO2KOPG7w5Sc9) (§5 Transcription, §6 schema, config sketch `stt.engine` / `stt.model`).

**Exit criterion:** for a stored alert clip, SQLite has non-null `transcript` and a populated `transcript_conf` (0..1 or model avg logprob mapped into that column).

---

## Goals

- Transcribe NWR alert WAV clips **locally** on the Protectli host (CPU-only is fine).
- Persist full transcript text + confidence into the existing `alerts` row.
- Keep demod / SAME / clip cut **real-time**: STT runs **async after** clip close.
- Stay privacy-first: audio + text stay on the LAN; **no cloud STT**.

## Non-goals

- No Whisper / STT application code in this phase of the docs PR (this file is the contract).
- No Phase 3 notify / Slack / webhook work.
- No move off Protectli; no Proxmox; do not touch the home-security SDR machine.
- No cloud STT (OpenAI, Google, etc.) for MVP.
- Do not rewrite Phase 1 listen path or change SAME decode behavior.

---

## Gate before coding

**Do not start Phase 2 implementation until Phase 1 SAME false-positive rate is acceptable.**

Phase 1 is live under `nwr-alerts.service` on Protectli. Baseline after 2026-09-16:

- Local noon RWT decoded (SAME + WAV).
- SQLite inserts fixed on main (`98f795c`, `check_same_thread=False` in `phase1/db.py`).

Before writing STT code, confirm with recent `query_alerts.sh` / journal that Dane-filtered alerts look real (not spam headers), then get an explicit go from Stephen or Chief of Staff.

---

## Engines

| Preference | Notes |
|------------|--------|
| **Primary** | Local Whisper or **whisper.cpp** (`tiny` / `base` / `small`, prefer `*.en`). CPU-only OK for short clips. |
| **Privacy** | Keep audio + text on LAN; no cloud STT. |
| **Fallback** | Vosk or faster-whisper **only if** primary quality fails on NWR voice. |

NWR announcers are trained and relatively clean; `base.en` or `small.en` is usually enough.

Suggested config keys (see `config/phase2.env.example`):

- `STT_ENGINE=whisper_cpp` (design: `stt.engine: whisper_cpp`)
- `STT_MODEL=base.en` (design: `stt.model: base.en`)

---

## Architecture hook (Phase 1)

Today `phase1/listen.py`:

1. `rtl_fm` → ring buffer + `multimon-ng` SAME/EAS.
2. On header → start `ClipWriter`; `AlertDB.insert(...)` (often with `audio_path=None`).
3. On `NNNN` / timeout / shutdown → `close_clip(...)` writes WAV under `$DATA_DIR/audio/`, then `AlertDB.update_audio_path(alert_id, wav_path)`.

**Phase 2 hook:** immediately after a successful `close_clip` WAV finalize (and after `update_audio_path`), **enqueue** async STT for `(alert_id, wav_path)`. Never run STT on the demod/SAME threads or inside the `rtl_fm` read loop.

Implementation options (pick one; document in code when built):

1. **Thread / asyncio queue** inside the listen process (simplest; must not starve audio).
2. **Side worker** watching new WAVs / a job table (cleaner isolation from `nwr-alerts.service`).
3. **systemd path unit / oneshot** triggered by file drop (optional later).

Until the worker exists, leave `nwr-alerts.service` as Phase 1-only. Do not block service start on model download.

---

## Database

`phase1/db.py` `SCHEMA` already defines:

- `transcript TEXT`
- `transcript_conf REAL`

`insert()` does **not** fill them yet. Implementer should add something like:

```python
def update_transcript(
    self,
    alert_id: int,
    transcript: str,
    transcript_conf: float | None,
    *,
    transcript_segments: str | None = None,  # only if column added
) -> None: ...
```

**Optional:** `transcript_segments TEXT` (JSON array of `{start, end, text, conf}`) if you want segment detail. Prefer keeping the schema lean; full text + scalar conf satisfies the Phase 2 exit criterion. If you add a column, migrate existing Protectli DBs with `ALTER TABLE ... ADD COLUMN` (SQLite).

Dedup: do not create a second alert row for STT. Update the row that owns the WAV. Re-runs of STT should overwrite `transcript` / `transcript_conf` for that `id`.

---

## Paths / storage

| Path | Role |
|------|------|
| Protectli clone | `~/NOAA/nwr-sdr-smoke` |
| Default `DATA_DIR` | checkout-relative `data/` (`data/alerts.db`, `data/audio/*.wav`) from Phase 1 |
| Design brief alt | `/var/lib/nwr-alerts/` — supported if operator sets `DATA_DIR` / documents absolute paths |

Prefer existing `data/` unless Stephen chooses the `/var/lib` layout. Models: e.g. `data/models/ggml-base.en.bin` (gitignored) or a documented absolute path in `phase2.env`.

Disk: WAV grows; design notes FLAC or downsample **after** STT if retention becomes an issue (not required for first STT slice).

---

## Dependencies (Protectli sketch)

Exact pins are implementer choice; sketch only:

```bash
# example: whisper.cpp
sudo apt-get install -y build-essential cmake git
# clone/build whisper.cpp somewhere under ~/NOAA/ or /opt; download ggml-base.en.bin
# OR: pip install in a venv (faster-whisper / openai-whisper) — still local CPU, no cloud API
```

Document the chosen binary path in `config/phase2.env` (`WHISPER_CPP_BIN`, `STT_MODEL_PATH`). Keep heavy deps out of the default Phase 1 apt list until Phase 2 is enabled.

---

## Config

Copy skeleton:

```bash
cp config/phase2.env.example config/phase2.env
# optional: source alongside phase1.env from a future run_phase2 / worker script
```

See `config/phase2.env.example` for `STT_*` / path knobs. No API keys.

---

## Success / exit criteria

1. Offline: given an existing alert WAV, STT writes `transcript` + `transcript_conf` on that alert’s row; `query_alerts.sh` (or `sqlite3`) shows them.
2. Live: next RWT / real SAME that Phase 1 stores also gets transcript fields without dropping demod/SAME (no audio underruns, no missed headers while STT runs).
3. Failure mode: STT errors log and leave transcript null; they must **not** crash `listen.py` or stop `rtl_fm`.

---

## Test plan

1. **Offline replay:** pick WAVs already under `data/audio/` on Protectli (or any saved RWT clip). Run the STT CLI/worker against one file; assert DB update for a known `id` (or a dry-run that prints text + conf).
2. **Quality spot-check:** listen to clip vs transcript; if garbage, try `small.en` or fallback engine before changing architecture.
3. **Live:** leave Phase 1 service running; enable async hook; wait for next Wednesday noon RWT (or real alert); confirm row has audio_path **and** transcript fields.
4. **Load:** confirm journal shows continuous `rtl_fm` / multimon while a long clip is transcribed.

---

## Ops notes

- Host: **Protectli only**.
- Service: `systemctl --user status nwr-alerts.service` must keep working if STT is disabled or broken.
- Do not require model files for Phase 1 install scripts.
- Ownership: Software bot owns app code; merge only with Stephen / CoS yes.

---

## Suggested file layout when implementing (later)

```
phase2/                 # optional package
  stt_whisper_cpp.py    # or subprocess wrapper
  worker.py             # queue consumer
scripts/run_phase2_worker.sh
config/phase2.env       # gitignored, from example
docs/PHASE2.md          # this file
```

Until then, this document + `config/phase2.env.example` are the Phase 2 deliverable.
