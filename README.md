# NWR SDR smoke test (Phase 0)

Minimal check that the RTL-SDR on the Protectli Linux host can receive
NOAA Weather Radio. This is **not** the full alert pipeline (no SAME,
no SQLite, no Whisper). Design target:
[NOAA SDR Weather Alert Pipeline Design v2](https://drive.google.com/file/d/16R4MZVRLa44BPlTqiuuWO2KOPG7w5Sc9).

Madison baseline from that brief: **WXJ-87 at 162.550 MHz**.

## What it does

1. Confirms the RTL-SDR is visible (`rtl_test` / `lsusb`).
2. Warns if the in-kernel `rtl2832` / `dvb_usb_rtl28xxu` stack still owns
   the stick (common when `swradio0` is present) — librtlsdr cannot
   open it until those modules are unloaded or blacklisted.
3. Records a short NBFM clip on 162.550 MHz with `rtl_fm` + `sox`.
4. Scores the WAV (RMS / peak) so you can tell voice/carrier from dead air
   without listening, then prints where the file landed.

## Packages (Protectli)

Debian/Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y rtl-sdr sox python3
```

Confirm the stick:

```bash
lsusb | grep -i -E 'Realtek|RTL|2838|0bda'
rtl_test -t
```

If `rtl_test` fails with “usb_claim_interface error” / “device or
resource busy”, the kernel driver still holds the dongle. Either:

```bash
# one-shot for this session
sudo modprobe -r dvb_usb_rtl28xxu rtl2832_sdr rtl2832 dvb_usb_v2 2>/dev/null || true
sudo modprobe -r rtl2832_sdr rtl2832 2>/dev/null || true
```

or install the blacklist helper in this repo and reboot:

```bash
sudo ./scripts/blacklist_kernel_sdr.sh
sudo reboot
```

Home-security SDR stays on its own machine. Do not move it here.

## Run (Protectli)

Copy this directory onto the Protectli (scp/rsync/git clone — whatever
you use). Then:

```bash
cd /path/to/nwr-sdr-smoke
cp config/smoke.env.example config/smoke.env   # edit if needed
./scripts/smoke_test.sh
```

Optional overrides:

```bash
DURATION_S=30 GAIN=40 ./scripts/smoke_test.sh
DEVICE_INDEX=0 FREQ_HZ=162550000 ./scripts/smoke_test.sh
```

Output WAV lands under `samples/` (gitignored). Success criteria are
printed at the end of the script.

## Success

- `rtl_test -t` finds the R820T / RTL2832U and does not hang on claim.
- A WAV is written under `samples/`.
- `scripts/check_audio.py` reports RMS well above the silence floor
  (default fail if RMS < about −50 dBFS for a 15–30 s clip on a live
  NWR carrier — tune thresholds in `config/smoke.env` if your gain/PPM
  differ).
- Optional ear check: `play samples/*.wav` (sox) — continuous NWR voice
  or the Wednesday ~noon MKX weekly test.

## Not in this smoke test

SAME / multimon-ng, clip cutting, SQLite, Whisper, systemd service,
udev `/dev/nwr_sdr` binding. Those are Phase 1+ in the design brief.
