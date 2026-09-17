# NWR SDR — Protectli NOAA Weather Radio

Phase 0 smoke test + Phase 1 SAME listener for an RTL-SDR on a Protectli
Linux host. Design brief:
[NOAA SDR Weather Alert Pipeline Design v2](https://drive.google.com/file/d/16R4MZVRLa44BPlTqiuuWO2KOPG7w5Sc9).

Madison baseline: **WXJ-87 at 162.550 MHz**. Dane FIPS `055025`.

Public repo: https://github.com/sbj-ee/nwr-sdr-smoke

Home-security SDR stays on its own machine. Do not move it here. Do not
buy another dongle for this.

## Status

| Phase | What | State |
|------|------|--------|
| 0 | `rtl_test` + short WAV + energy score | done (`scripts/smoke_test.sh`) |
| 1 | Continuous listen, multimon-ng SAME, clip cut, SQLite | done (`phase1/listen.py`) |
| 2 | Local Whisper transcript | done (`phase2/worker.py`) — disabled by default, see below |
| 3 | Notify webhook / UI | done (`phase3/notify_worker.py`) — disabled, needs a hub token, see below |

## Packages (Protectli)

```bash
sudo apt-get update
sudo apt-get install -y rtl-sdr sox python3 git psmisc multimon-ng sqlite3
git clone https://github.com/sbj-ee/nwr-sdr-smoke.git
cd nwr-sdr-smoke
```

### Kernel DVB vs librtlsdr (required)

Ubuntu’s in-kernel `rtl2832_sdr` / `dvb_usb_rtl28xxu` stack creates
`/dev/swradio0` and blocks `rtl_fm`. Permanent fix on the Protectli NWR
host only:

```bash
sudo ./scripts/blacklist_kernel_sdr.sh
sudo reboot
```

Session-only:

```bash
sudo ./scripts/unload_kernel_sdr.sh
```

### Permissions (libusb error -3)

```bash
sudo ./scripts/install_udev_rules.sh "$USER"
# unplug/replug stick; log out/in for plugdev
```

Interim: `USE_SUDO_RTL=1` on smoke test / Phase 1.

## Phase 0 — smoke test

```bash
cp config/smoke.env.example config/smoke.env
./scripts/smoke_test.sh
# or: AUTO_UNLOAD=1 USE_SUDO_RTL=1 ./scripts/smoke_test.sh
```

Success: `PASS: audio energy looks like live RF` and a WAV under `samples/`.

## Phase 1 — SAME + clip + SQLite

Listens continuously on the configured NWR frequency, runs
`multimon-ng -a EAS` (Ubuntu 1.3.0; falls back to `SAME` if that demod exists) on the demod audio, cuts a WAV from a few seconds
before the `ZCZC` header until `NNNN` (or `MAX_CLIP_S`), and inserts a
row into SQLite (`data/alerts.db` by default). Dedups by
event + FIPS + issue time + frequency within the purge window.

```bash
cp config/phase1.env.example config/phase1.env
# edit REQUIRE_COUNTY_FIPS / DATA_DIR / USE_SUDO_RTL if needed
./scripts/run_phase1.sh
```

Dry-run (log SAME only, no DB/WAV):

```bash
./scripts/run_phase1.sh --dry-run
```

Query recent rows:

```bash
./scripts/query_alerts.sh
```

### systemd (survive disconnect / reboot)

Prefer a **user** unit as `stevebj` (clone path `~/NOAA/nwr-sdr-smoke`).
Stop any foreground/`nohup` listener first. Kernel DVB blacklist should
already be applied.

```bash
cd ~/NOAA/nwr-sdr-smoke && git pull
# stop ad-hoc runs (install script also tries this)
pkill -f 'phase1/listen.py' 2>/dev/null || true
pkill -x rtl_fm 2>/dev/null || true
./scripts/install_systemd_user.sh
```

That installs `~/.config/systemd/user/nwr-alerts.service`, enables it,
starts it, and turns on `loginctl` linger so it comes up at boot without
a login session.

```bash
systemctl --user status nwr-alerts.service
journalctl --user -u nwr-alerts.service -f
systemctl --user restart nwr-alerts.service
systemctl --user stop nwr-alerts.service
```

If the repo is not at `~/NOAA/nwr-sdr-smoke`, edit paths in
`systemd/user/nwr-alerts.service` (or symlink the clone) then
`FORCE_INSTALL=1 ./scripts/install_systemd_user.sh`.

Optional system unit (root): `systemd/nwr-alerts.service` — copy to
`/etc/systemd/system/`, `daemon-reload`, `enable --now`.

### Phase 1 success

- Process stays up; `rtl_fm` does not EOF immediately.
- On a SAME burst (Wednesday ~noon MKX **RWT** is the easy test), logs show
  `multimon: EAS: ZCZC-...` then later `NNNN`.
- A WAV appears under `data/audio/`.
- `./scripts/query_alerts.sh` shows a row with event / FIPS / `audio_path`.
- Re-broadcast within the purge window logs as duplicate (same `dedup_key`).

## Phase 2 — local transcript

Spec: **[docs/PHASE2.md](docs/PHASE2.md)**. Implemented as a standalone poller
(`phase2/worker.py`), separate from `nwr-alerts.service`, so a stuck or
crashing STT run can never starve `rtl_fm`/`multimon-ng` or take down Phase 1.
It polls `alerts` for rows with `audio_path` set and `transcript` still null,
transcribes with local **whisper.cpp**, and writes `transcript` /
`transcript_conf` back. No cloud STT.

### Setup (Protectli)

```bash
sudo apt-get install -y build-essential cmake git
git clone https://github.com/ggml-org/whisper.cpp.git ~/NOAA/whisper.cpp
cmake -B ~/NOAA/whisper.cpp/build -DCMAKE_BUILD_TYPE=Release ~/NOAA/whisper.cpp
cmake --build ~/NOAA/whisper.cpp/build -j"$(nproc)"
bash ~/NOAA/whisper.cpp/models/download-ggml-model.sh base.en
mkdir -p data/models
cp ~/NOAA/whisper.cpp/models/ggml-base.en.bin data/models/ggml-base.en.bin

cp config/phase2.env.example config/phase2.env
# set STT_ENABLED=1 when ready; WHISPER_CPP_BIN already points at the build above
```

### Run

```bash
# offline: transcribe one WAV, print text + confidence (no DB write)
python3 phase2/worker.py --file data/audio/20260916T170132Z-RWT.wav

# offline: transcribe one WAV and write it to a specific alert row
python3 phase2/worker.py --file path/to/clip.wav --alert-id 7

# one pass over all pending rows (audio_path set, transcript null), then exit
python3 phase2/worker.py --once

# poll loop (what the systemd unit runs)
python3 phase2/worker.py
```

### systemd (optional, not installed automatically)

```bash
install -m 0644 systemd/user/nwr-phase2-worker.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nwr-phase2-worker.service
```

Exits cleanly (no restart loop) if `STT_ENABLED` isn't `1` in `config/phase2.env`.

### Notes

- Verified end-to-end through `phase2/worker.py --file` against a real
  RWT clip (`data/audio/20260916T170132Z-RWT.wav`, `base.en`, Protectli's
  Celeron N5105): whisper.cpp's own defaults (`beam_size=5`/`best_of=5`)
  took **4m21s** wall clock for a ~90s clip and produced an accurate
  transcript (`confidence: 0.753`; minor misses on a couple of county
  names, expected for `base.en` on unusual proper nouns). Tried forcing
  greedy decoding (`beam_size=1`/`best_of=1`) to cut latency — it was
  **not** meaningfully faster (the CPU-bound encoder pass dominates, not
  beam search) and quality was visibly worse: it dropped the entire
  county-name read-out as `[static]`. `phase2/worker.py` keeps
  whisper.cpp's defaults.
- Confidence is the mean per-token probability whisper.cpp reports (`-ojf`
  JSON), not a calibrated 0..1 score — treat it as relative, not absolute.
- STT errors are logged and leave `transcript` null; they never crash the
  worker or touch `nwr-alerts.service`.

## Phase 3 — notify + query

Spec: **[docs/PHASE3.md](docs/PHASE3.md)**. Implemented as a standalone poller
(`phase3/notify_worker.py`), same isolation pattern as Phase 2 — it never
imports or touches `phase1/listen.py`, so a hub outage or a stuck HTTP call
can't affect capture or transcription. It polls `alerts` for rows that
haven't been through a notify decision yet (`notified_at IS NULL`), gates
them by severity/test/unknown rules, and POSTs eligible ones to
**ts-notify-hub** topic `house-nwr` (+ `house-urgent` for warning-class) over
Tailscale. Not a WEA replacement.

**This system is not a substitute for phone WEA or a battery SAME radio.**

### Setup (Protectli)

Tailscale is already up and `debian199` is reachable
(`curl http://debian199.tailade1d3.ts.net:8787/health` → `{"ok":true}`).
What's missing is a **publish-scoped bearer token**, minted on the hub —
this implementer had no SSH access to `debian199` to mint one.

```bash
cp config/phase3.env.example config/phase3.env
# mint a publish-scoped token on debian199 (ts-notify-hub admin CLI) and set:
#   NOTIFY_TOKEN=<token>
#   NOTIFY_ENABLED=1   # when ready
```

### Run

```bash
# dry-run: log the JSON that would be POSTed, no HTTP, no DB writes — safe with no token
python3 phase3/notify_worker.py --once --dry-run

# one pass over pending rows (notified_at IS NULL), then exit
python3 phase3/notify_worker.py --once

# poll loop (what the systemd unit runs)
python3 phase3/notify_worker.py
```

### Query

```bash
./scripts/query_alerts.sh                                  # last 20 rows (back-compat)
./scripts/query_alerts.sh --event TOR
./scripts/query_alerts.sh --fips 055025
./scripts/query_alerts.sh --since 2026-09-01 --until 2026-09-17
./scripts/query_alerts.sh --event SVR --fips 055025 --since 2026-09-01
```

Delegates to `phase3/query.py` (parameterized SQL, no shell-injection risk).

### systemd (optional, not installed automatically)

```bash
install -m 0644 systemd/user/nwr-phase3-notify.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now nwr-phase3-notify.service
```

Exits cleanly (no restart loop) if `NOTIFY_ENABLED` isn't `1` in `config/phase3.env`.

### Severity gate

| `NOTIFY_MIN_CLASS` | Notifies |
|---|---|
| `warning` (default) | `warning` only |
| `watch` | `watch` or `warning` |
| `advisory` | `advisory`, `watch`, or `warning` |
| `all` | any class, still subject to test/unknown suppression |

RWT/RMT/NPT/DMO (`event_class=test`) suppressed unless `NOTIFY_TESTS=1`.
Unknown SAME codes (`event_class=other`) suppressed unless `NOTIFY_UNKNOWN=1`.
Suppressed rows are marked `notify_topic=suppressed:<reason>` so they're
not re-evaluated every poll, without ever POSTing to the hub.

### Notes

- Verified real HTTP contract against the live hub: with an invalid
  token, `POST /v1/publish/house-nwr` returned `401 {"error":"unauthorized"}`
  exactly as the spec's publish contract implies — confirms the endpoint
  shape without needing a real token. On that failure the row is left
  unnotified (retried next poll), never marked as sent.
- Verified end-to-end against a synthetic warning/watch/test row set in a
  scratch DB (not the real `alerts.db`): warning-class correctly built a
  dual `[house-nwr, house-urgent]` payload with the Dane FIPS flag and
  transcript preview; watch was suppressed under the default
  `NOTIFY_MIN_CLASS=warning`; RWT was suppressed as a test. Dry-run is
  idempotent (repeated runs produce identical output, nothing marked).
- Migrated the live production `data/alerts.db` (`notified_at`,
  `notify_topic` columns) while `nwr-alerts.service` and
  `nwr-phase2-worker.service` were both running; both stayed `active`
  throughout, row count unchanged.
- Every row in `alerts` is already a first-time insert — Phase 1's dedup
  never creates a second row for a repeat broadcast within the purge
  window — so there's no separate duplicate check; `notified_at` alone
  prevents re-notifying.
- No real (non-test) alert or valid hub token yet, so the **live** exit
  criterion (real alert → hub POST → visible to a subscribed client) is
  still open. `NOTIFY_ENABLED=0` until a token is set.

## Layout

```
scripts/smoke_test.sh          Phase 0
scripts/unload_kernel_sdr.sh   free /dev/swradio0
scripts/blacklist_kernel_sdr.sh
scripts/install_udev_rules.sh
scripts/run_phase1.sh
scripts/query_alerts.sh
scripts/install_systemd_user.sh
phase1/listen.py               Phase 1 supervisor
phase1/same.py                 SAME parse
phase1/db.py                   SQLite
phase2/worker.py                Phase 2 STT poller
phase2/stt_whisper_cpp.py        whisper.cpp subprocess wrapper
phase3/notify_worker.py         Phase 3 notify poller
phase3/notify.py                 eligibility / payload / hub HTTP
phase3/query.py                  CLI search (event/date/FIPS)
config/*.env.example
docs/PHASE2.md                 Phase 2 STT implementer spec
docs/PHASE3.md                 Phase 3 notify + query implementer spec
udev/99-rtl-sdr-nwr.rules
systemd/user/nwr-alerts.service          (preferred)
systemd/user/nwr-phase2-worker.service   (optional)
systemd/user/nwr-phase3-notify.service   (optional)
systemd/nwr-alerts.service        (system, optional)
```

No API tokens or passwords are required for Phase 0/1. Keep
`config/*.env` out of git (gitignored).


## Troubleshooting (Phase 1)

- `invalid mode "SAME"`: Ubuntu `multimon-ng` 1.3.0 names the decoder **EAS**, not SAME.
  The listener auto-selects `EAS`. Force with `MULTIMON_DEMOD=EAS` in `config/phase1.env`.
- `TypeError: write() argument must be str, not bytes`: fixed in current main — multimon
  stdin is binary. `git pull` and retry.
- `multimon-ng exited early`: usually wrong `-a` mode or missing package. Install
  `multimon-ng`, confirm with `multimon-ng -a EAS -h` / help text listing EAS.
