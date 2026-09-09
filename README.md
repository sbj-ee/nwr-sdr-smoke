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
| 2 | Local Whisper transcript | not started |
| 3 | Notify webhook / UI | not started |

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

Optional systemd (edit `User=` / paths first):

```bash
sudo cp systemd/nwr-alerts.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nwr-alerts.service
journalctl -u nwr-alerts.service -f
```

### Phase 1 success

- Process stays up; `rtl_fm` does not EOF immediately.
- On a SAME burst (Wednesday ~noon MKX **RWT** is the easy test), logs show
  `multimon: EAS: ZCZC-...` then later `NNNN`.
- A WAV appears under `data/audio/`.
- `./scripts/query_alerts.sh` shows a row with event / FIPS / `audio_path`.
- Re-broadcast within the purge window logs as duplicate (same `dedup_key`).

## Layout

```
scripts/smoke_test.sh          Phase 0
scripts/unload_kernel_sdr.sh   free /dev/swradio0
scripts/blacklist_kernel_sdr.sh
scripts/install_udev_rules.sh
scripts/run_phase1.sh
scripts/query_alerts.sh
phase1/listen.py               Phase 1 supervisor
phase1/same.py                 SAME parse
phase1/db.py                   SQLite
config/*.env.example
udev/99-rtl-sdr-nwr.rules
systemd/nwr-alerts.service
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
