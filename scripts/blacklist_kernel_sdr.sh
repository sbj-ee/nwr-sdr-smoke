#!/usr/bin/env bash
# Park the in-kernel RTL2832 / DVB stack so librtlsdr (rtl_fm / rtl_test)
# can claim the Protectli NWR stick. Safe on a host that is *only* using
# this dongle for userspace SDR — do not run on the home-security machine.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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
"$ROOT/scripts/unload_kernel_sdr.sh" || {
  echo "session unload incomplete — reboot after blacklist so modules stay down"
  exit 1
}
echo "done. if modules come back after reboot, check $RULE and initramfs."
