#!/usr/bin/env bash
# Park the in-kernel RTL2832 / DVB stack so librtlsdr (rtl_fm / rtl_test)
# can claim the Protectli NWR stick. Safe on a host that is *only* using
# this dongle for userspace SDR — do not run on the home-security machine.
set -euo pipefail

RULE=/etc/modprobe.d/blacklist-nwr-sdr.conf
if [[ $EUID -ne 0 ]]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

cat >"$RULE" <<'CONF'
# NWR Protectli: prefer librtlsdr over in-kernel DVB/SDR for this stick.
blacklist dvb_usb_rtl28xxu
blacklist dvb_usb_v2
blacklist rtl2832
blacklist rtl2832_sdr
blacklist rtl2830
blacklist dvb_usb
CONF

echo "wrote $RULE"
echo "unloading modules for this session (ignore errors if already free)..."
modprobe -r dvb_usb_rtl28xxu 2>/dev/null || true
modprobe -r rtl2832_sdr 2>/dev/null || true
modprobe -r rtl2832 2>/dev/null || true
modprobe -r dvb_usb_v2 2>/dev/null || true
echo "done. if rtl_test still fails, reboot so the blacklist sticks."
