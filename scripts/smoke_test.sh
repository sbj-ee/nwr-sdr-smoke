#!/usr/bin/env bash
# Phase 0 NWR smoke test for Protectli + RTL-SDR (R820T / RTL2832U).
# Proves the stick can open, tune WXJ-87 (162.550 MHz), and record non-silent audio.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${ENV_FILE:-$ROOT/config/smoke.env}"
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  # shellcheck disable=SC1091
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

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "FAIL: missing command '$1' — install rtl-sdr / sox / python3" >&2
    exit 1
  }
}

need rtl_test
need rtl_fm
need sox
need python3

echo "== host / USB =="
uname -a || true
lsusb | grep -iE 'Realtek|RTL|2838|0bda|SDR' || echo "WARN: no obvious RTL line in lsusb"

echo
echo "== kernel SDR modules (problem if still loaded) =="
if lsmod | grep -iE 'rtl283|dvb_usb_rtl|rtl28' ; then
  echo "WARN: in-kernel RTL/DVB modules are loaded. librtlsdr often cannot claim the stick."
  echo "      Fix for this session:"
  echo "        sudo modprobe -r dvb_usb_rtl28xxu rtl2832_sdr rtl2832 dvb_usb_v2"
  echo "      Or permanent: sudo ./scripts/blacklist_kernel_sdr.sh && sudo reboot"
else
  echo "OK: no rtl283*/dvb_usb_rtl* modules loaded"
fi

if [[ -e /dev/swradio0 ]]; then
  echo "WARN: /dev/swradio0 exists (kernel SDR path). Prefer unloading those modules before rtl_fm."
fi

echo
echo "== rtl_test (device claim) =="
# -t exits after a short EEPROM/tuner probe on recent rtl-sdr; if it hangs,
# Ctrl-C and fix the kernel-module claim first.
if ! timeout 15 rtl_test -t -d "$DEVICE_INDEX"; then
  echo "FAIL: rtl_test could not open device index $DEVICE_INDEX" >&2
  echo "      Check USB, DEVICE_INDEX, and kernel module ownership." >&2
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

# rtl_fm writes signed 16-bit LE mono at SAMPLE_RATE.
# Capture raw, then convert with sox so the WAV header is clean.
set +e
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
rtl_rc=$?
set -e
# timeout returns 124 when the duration elapses — that is success here.
if [[ $rtl_rc -ne 0 && $rtl_rc -ne 124 ]]; then
  echo "FAIL: rtl_fm exited $rtl_rc" >&2
  exit 1
fi

if [[ ! -s "$RAW" ]]; then
  echo "FAIL: empty capture $RAW" >&2
  exit 1
fi

sox -t raw -r "$SAMPLE_RATE" -e signed -b 16 -c 1 "$RAW" "$OUT"
rm -f "$RAW"
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
