#!/usr/bin/env bash
# Phase 0 NWR smoke test for Protectli + RTL-SDR (R820T / RTL2832U).
# Proves the stick can open, tune WXJ-87 (162.550 MHz), and record non-silent audio.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${ENV_FILE:-$ROOT/config/smoke.env}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
elif [[ -f "$ROOT/config/smoke.env.example" ]]; then
  echo "NOTE: no config/smoke.env — using smoke.env.example defaults"
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/config/smoke.env.example"
  set +a
fi

FREQ_HZ="${FREQ_HZ:-162550000}"
DEVICE_INDEX="${DEVICE_INDEX:-0}"
DURATION_S="${DURATION_S:-20}"
GAIN="${GAIN:-0}"
PPM="${PPM:-0}"
SAMPLE_RATE="${SAMPLE_RATE:-22050}"
MIN_RMS_DBFS="${MIN_RMS_DBFS:--50}"
MIN_PEAK_DBFS="${MIN_PEAK_DBFS:--25}"
# Set AUTO_UNLOAD=1 to run scripts/unload_kernel_sdr.sh via sudo before rtl_test.
AUTO_UNLOAD="${AUTO_UNLOAD:-0}"
# Set USE_SUDO_RTL=1 to prefix rtl_test/rtl_fm with sudo (first claim / no udev yet).
USE_SUDO_RTL="${USE_SUDO_RTL:-0}"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "FAIL: missing command '$1' — install rtl-sdr / sox / python3" >&2
    exit 1
  }
}

print_access_fix() {
  cat <<'HINT'

FIX (libusb error -3 = permission / no udev rule):
  1. Install rules + plugdev (once):
       sudo ./scripts/install_udev_rules.sh "$USER"
  2. Unplug/replug the stick (or reboot), then log out/in so plugdev applies.
  3. Retry:
       ./scripts/smoke_test.sh
  First claim without udev yet:
       USE_SUDO_RTL=1 ./scripts/smoke_test.sh
       # or: sudo rtl_test -t
HINT
}

print_kernel_fix() {
  cat <<'HINT'

FIX (kernel still owns the stick — /dev/swradio0 or rtl2832* loaded):
  Aggressive session unload:
       sudo ./scripts/unload_kernel_sdr.sh
  Permanent (blacklist + unload; reboot if still busy):
       sudo ./scripts/blacklist_kernel_sdr.sh
       sudo reboot
  Then:
       ./scripts/smoke_test.sh
HINT
}

need rtl_test
need rtl_fm
need sox
need python3

echo "== host / USB =="
uname -a || true
lsusb | grep -iE 'Realtek|RTL|2838|0bda|SDR' || echo "WARN: no obvious RTL line in lsusb"

echo
echo "== kernel SDR modules =="
kernel_busy=0
if lsmod | grep -iE 'rtl283|dvb_usb_rtl|rtl28' ; then
  kernel_busy=1
  echo "WARN: in-kernel RTL/DVB modules are loaded."
else
  echo "OK: no rtl283*/dvb_usb_rtl* modules loaded"
fi
if [[ -e /dev/swradio0 ]]; then
  kernel_busy=1
  echo "WARN: /dev/swradio0 exists (kernel SDR path)."
  fuser -v /dev/swradio0 2>&1 || true
fi

if [[ "$kernel_busy" -eq 1 ]]; then
  if [[ "$AUTO_UNLOAD" == "1" ]]; then
    echo "AUTO_UNLOAD=1 — running sudo ./scripts/unload_kernel_sdr.sh"
    sudo "$ROOT/scripts/unload_kernel_sdr.sh" || {
      print_kernel_fix
      exit 1
    }
  else
    echo "Will not open reliably until those modules are gone."
    print_kernel_fix
    echo "Or re-run with: AUTO_UNLOAD=1 ./scripts/smoke_test.sh"
    # Keep going — rtl_test error text may still diagnose permissions.
  fi
fi

echo
echo "== rtl_test (device claim) =="
# Capture stderr: error -3 is LIBUSB_ERROR_ACCESS; busy/claim is kernel ownership.
set +e
if [[ "$USE_SUDO_RTL" == "1" ]]; then
  rtl_out="$(timeout 15 sudo rtl_test -t -d "$DEVICE_INDEX" 2>&1)"
else
  rtl_out="$(timeout 15 rtl_test -t -d "$DEVICE_INDEX" 2>&1)"
fi
rtl_rc=$?
set -e
printf '%s\n' "$rtl_out"

if [[ $rtl_rc -ne 0 ]]; then
  echo "FAIL: rtl_test could not open device index $DEVICE_INDEX (exit $rtl_rc)" >&2
  if printf '%s' "$rtl_out" | grep -qE 'error -3|Failed to open|usb_open error -3|LIBUSB_ERROR_ACCESS|Access denied|permission'; then
    print_access_fix
  fi
  if printf '%s' "$rtl_out" | grep -qiE 'busy|claim|resource busy|error -6|LIBUSB_ERROR_BUSY' \
    || [[ -e /dev/swradio0 ]] \
    || lsmod | grep -qiE 'rtl283|dvb_usb_rtl'; then
    print_kernel_fix
  fi
  if [[ "$USE_SUDO_RTL" != "1" ]]; then
    echo
    echo "Quick bypass for this session: USE_SUDO_RTL=1 ./scripts/smoke_test.sh"
  fi
  exit 1
fi

mkdir -p "$ROOT/samples"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$ROOT/samples/nwr-${FREQ_HZ}-${TS}.wav"
RAW="$ROOT/samples/nwr-${FREQ_HZ}-${TS}.raw"

echo
echo "== record ${DURATION_S}s NBFM @ ${FREQ_HZ} Hz (device $DEVICE_INDEX) =="
GAIN_ARGS=()
if [[ "$GAIN" != "0" && -n "$GAIN" ]]; then
  GAIN_ARGS=(-g "$GAIN")
fi
PPM_ARGS=()
if [[ "$PPM" != "0" && -n "$PPM" ]]; then
  PPM_ARGS=(-p "$PPM")
fi

set +e
if [[ "$USE_SUDO_RTL" == "1" ]]; then
  timeout "$DURATION_S" sudo rtl_fm \
    -d "$DEVICE_INDEX" \
    -f "$FREQ_HZ" \
    -M fm \
    -s "$SAMPLE_RATE" \
    -A fast \
    -l 0 \
    -E deemp \
    "${PPM_ARGS[@]}" \
    "${GAIN_ARGS[@]}" \
    "$RAW"
else
  timeout "$DURATION_S" rtl_fm \
    -d "$DEVICE_INDEX" \
    -f "$FREQ_HZ" \
    -M fm \
    -s "$SAMPLE_RATE" \
    -A fast \
    -l 0 \
    -E deemp \
    "${PPM_ARGS[@]}" \
    "${GAIN_ARGS[@]}" \
    "$RAW"
fi
rtl_rc=$?
set -e
# timeout returns 124 when the duration elapses — that is success here.
if [[ $rtl_rc -ne 0 && $rtl_rc -ne 124 ]]; then
  echo "FAIL: rtl_fm exited $rtl_rc" >&2
  if [[ "$USE_SUDO_RTL" != "1" ]]; then
    echo "Retry with: USE_SUDO_RTL=1 ./scripts/smoke_test.sh" >&2
  fi
  exit 1
fi

if [[ ! -s "$RAW" ]]; then
  echo "FAIL: empty capture $RAW" >&2
  exit 1
fi

# rtl_fm under sudo leaves root-owned files; hand them back.
OWNER="${SUDO_USER:-$USER}"
if [[ "$USE_SUDO_RTL" == "1" && -f "$RAW" ]]; then
  sudo chown "$OWNER:" "$RAW" 2>/dev/null || true
fi

sox -t raw -r "$SAMPLE_RATE" -e signed -b 16 -c 1 "$RAW" "$OUT"
rm -f "$RAW"
if [[ "$USE_SUDO_RTL" == "1" && -f "$OUT" ]]; then
  sudo chown "$OWNER:" "$OUT" 2>/dev/null || true
fi
echo "wrote $OUT ($(wc -c <"$OUT") bytes)"

echo
echo "== audio score =="
python3 "$ROOT/scripts/check_audio.py" "$OUT" \
  --min-rms-dbfs "$MIN_RMS_DBFS" \
  --min-peak-dbfs "$MIN_PEAK_DBFS"
rc=$?

echo
if [[ $rc -eq 0 ]]; then
  echo "SUCCESS: Protectli NWR RF path looks alive on ${FREQ_HZ} Hz."
  echo "Listen: play $OUT"
  echo "Next (Phase 1, not this smoke test): multimon-ng SAME + clip cut + SQLite."
else
  echo "SMOKE TEST FAILED — capture exists but looks like dead air / too quiet."
  echo "Try: GAIN=40 ./scripts/smoke_test.sh"
  echo "     or FREQ_HZ=162425000 (WWG-90 Janesville) if WXJ-87 is weak."
fi
exit $rc
