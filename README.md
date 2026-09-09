# NWR SDR smoke test (Phase 0)

Minimal check that the RTL-SDR on the Protectli Linux host can receive
NOAA Weather Radio. This is **not** the full alert pipeline (no SAME,
no SQLite, no Whisper). Design target:
[NOAA SDR Weather Alert Pipeline Design v2](https://drive.google.com/file/d/16R4MZVRLa44BPlTqiuuWO2KOPG7w5Sc9).

Madison baseline from that brief: **WXJ-87 at 162.550 MHz**.

Public repo: https://github.com/sbj-ee/nwr-sdr-smoke

## What it does

1. Confirms the RTL-SDR is visible (`rtl_test` / `lsusb`).
2. Detects in-kernel `rtl2832` / `dvb_usb_rtl28xxu` ownership (`/dev/swradio0`)
   and prints an aggressive unload path — librtlsdr cannot open the stick
   until those modules are gone.
3. Detects libusb **error -3** (permission) and prints the udev / `plugdev` fix;
   optional `USE_SUDO_RTL=1` for a first claim before udev is installed.
4. Records a short NBFM clip on 162.550 MHz with `rtl_fm` + `sox`.
5. Scores the WAV (RMS / peak) so you can tell voice/carrier from dead air.

## Packages (Protectli)

```bash
sudo apt-get update
sudo apt-get install -y rtl-sdr sox python3 git psmisc
git clone https://github.com/sbj-ee/nwr-sdr-smoke.git
cd nwr-sdr-smoke
cp config/smoke.env.example config/smoke.env
```

`psmisc` provides `fuser` (used by the unload helper).

## Protectli: two common failures

### A. Kernel still holds the stick

Symptoms: `rtl2832_sdr` / `dvb_usb_rtl28xxu` still in `lsmod`, `/dev/swradio0`
present, `modprobe -r ...` appears to do nothing.

```bash
sudo ./scripts/unload_kernel_sdr.sh
# or let the smoke test call it:
AUTO_UNLOAD=1 ./scripts/smoke_test.sh
```

Permanent (do **not** run this on the home-security SDR host):

```bash
sudo ./scripts/blacklist_kernel_sdr.sh
sudo reboot
```

### B. `usb_open error -3` (permissions / missing udev)

USB is present (`lsusb` shows `0bda:2838`) but your user cannot open it.

```bash
sudo ./scripts/install_udev_rules.sh "$USER"
# unplug/replug the stick (or reboot), then log out/in for plugdev
./scripts/smoke_test.sh
```

First claim before udev is sorted:

```bash
USE_SUDO_RTL=1 ./scripts/smoke_test.sh
```

Home-security SDR stays on its own machine. Do not move it here.

## Run

```bash
./scripts/smoke_test.sh
```

Optional overrides:

```bash
DURATION_S=30 GAIN=40 ./scripts/smoke_test.sh
AUTO_UNLOAD=1 USE_SUDO_RTL=1 ./scripts/smoke_test.sh
DEVICE_INDEX=0 FREQ_HZ=162550000 ./scripts/smoke_test.sh
```

Output WAV lands under `samples/` (gitignored).

## Success

- `rtl_test -t` opens the R820T / RTL2832U (no error -3, no busy).
- A WAV is written under `samples/`.
- `scripts/check_audio.py` prints `PASS` (RMS/peak above floors in
  `config/smoke.env`).
- Optional ear check: `play samples/*.wav`.

## Not in this smoke test

SAME / multimon-ng, clip cutting, SQLite, Whisper, systemd service,
serial-based `/dev/nwr_sdr` binding. Those are Phase 1+ in the design brief.
