#!/usr/bin/env bash
# Install udev rules + plugdev membership so rtl_test/rtl_fm work without
# sudo after re-plug. Fixes libusb error -3 (LIBUSB_ERROR_ACCESS).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RULE_SRC="$ROOT/udev/99-rtl-sdr-nwr.rules"
RULE_DST=/etc/udev/rules.d/99-rtl-sdr-nwr.rules

if [[ $EUID -ne 0 ]]; then
  echo "run as root: sudo $0 [username]" >&2
  exit 1
fi

TARGET_USER="${1:-${SUDO_USER:-}}"
if [[ -z "$TARGET_USER" || "$TARGET_USER" == root ]]; then
  echo "usage: sudo $0 <username-to-add-to-plugdev>" >&2
  exit 1
fi

install -m 0644 "$RULE_SRC" "$RULE_DST"
udevadm control --reload-rules
udevadm trigger

if getent group plugdev >/dev/null; then
  usermod -aG plugdev "$TARGET_USER"
  echo "added $TARGET_USER to plugdev (log out/in or newgrp plugdev for it to apply)"
else
  echo "WARN: no plugdev group on this host — MODE=0666 in the rule still applies"
fi

echo "wrote $RULE_DST"
echo "unplug/replug the RTL stick (or reboot), then:"
echo "  rtl_test -t"
echo "first claim can also use: sudo rtl_test -t"
