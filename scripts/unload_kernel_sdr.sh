#!/usr/bin/env bash
# Aggressively free the RTL stick from in-kernel DVB/SDR so librtlsdr can
# claim it. Run with sudo. Do not use on the home-security SDR host.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

echo "== holders of /dev/swradio* (if any) =="
for node in /dev/swradio*; do
  [[ -e "$node" ]] || continue
  echo "fuser -v $node:"
  fuser -v "$node" 2>&1 || true
  # Kill userspace holdouts that keep the module busy (not typical, but
  # blocks modprobe -r when present).
  fuser -k "$node" 2>/dev/null || true
done

# Dependency order: USB front-end first, then SDR/DVB core, then shared.
# One module per call — a multi-arg -r fails the whole set if one is busy.
mods=(
  dvb_usb_rtl28xxu
  rtl2832_sdr
  rtl2832
  rtl2830
  dvb_usb_v2
  dvb_usb
  dvb_core
)

echo
echo "== unloading modules one-by-one =="
still=()
for m in "${mods[@]}"; do
  if lsmod | awk '{print $1}' | grep -qx "$m"; then
    if modprobe -r "$m" 2>/tmp/nwr-modprobe-err; then
      echo "unloaded $m"
    else
      echo "BUSY/FAIL $m: $(tr '\n' ' ' </tmp/nwr-modprobe-err)"
      still+=("$m")
    fi
  else
    echo "not loaded: $m"
  fi
done

echo
if [[ -e /dev/swradio0 ]]; then
  echo "WARN: /dev/swradio0 still exists after unload attempts"
else
  echo "OK: /dev/swradio0 gone (or never present)"
fi

if ((${#still[@]})); then
  echo "FAIL: still loaded: ${still[*]}"
  echo "      Close anything using the stick, then retry, or:"
  echo "        sudo ./scripts/blacklist_kernel_sdr.sh && sudo reboot"
  exit 1
fi

echo "OK: kernel RTL/DVB modules cleared for this session"
