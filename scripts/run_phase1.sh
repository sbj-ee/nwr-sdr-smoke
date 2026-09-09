#!/usr/bin/env bash
# Start Phase 1 SAME listener on Protectli.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f config/phase1.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source config/phase1.env
  set +a
elif [[ -f config/phase1.env.example ]]; then
  echo "NOTE: using config/phase1.env.example (copy to phase1.env to customize)"
  set -a
  # shellcheck disable=SC1091
  source config/phase1.env.example
  set +a
fi

need() { command -v "$1" >/dev/null || { echo "missing $1" >&2; exit 1; }; }
need rtl_fm
need multimon-ng
need python3

if lsmod | grep -qiE 'rtl283|dvb_usb_rtl'; then
  echo "WARN: kernel RTL/DVB modules still loaded — librtlsdr may fail."
  echo "      sudo ./scripts/unload_kernel_sdr.sh"
  echo "      permanent: sudo ./scripts/blacklist_kernel_sdr.sh && sudo reboot"
fi

exec python3 "$ROOT/phase1/listen.py" "$@"
