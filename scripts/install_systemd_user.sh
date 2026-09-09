#!/usr/bin/env bash
# Install Phase 1 as a systemd --user service (survives logout with linger,
# restarts on failure, starts at boot).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_SRC="$ROOT/systemd/user/nwr-alerts.service"
UNIT_DST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/nwr-alerts.service"
EXPECTED="$HOME/NOAA/nwr-sdr-smoke"

if [[ "$ROOT" != "$EXPECTED" ]]; then
  echo "WARN: clone is at $ROOT"
  echo "      user unit WorkingDirectory is %h/NOAA/nwr-sdr-smoke (= $EXPECTED)."
  echo "      Either move/clone there, or edit WorkingDirectory/EnvironmentFile/ExecStart"
  echo "      in $UNIT_SRC before installing."
  if [[ "${FORCE_INSTALL:-0}" != "1" ]]; then
    echo "      Re-run with FORCE_INSTALL=1 to install anyway."
    exit 1
  fi
fi

if [[ ! -f "$ROOT/config/phase1.env" ]]; then
  echo "NOTE: no config/phase1.env — copying from example"
  cp "$ROOT/config/phase1.env.example" "$ROOT/config/phase1.env"
fi

# Stop ad-hoc runs so they don't fight the service for the stick
if pgrep -f 'phase1/listen.py' >/dev/null 2>&1; then
  echo "Stopping existing phase1/listen.py processes..."
  pkill -f 'phase1/listen.py' || true
  sleep 1
fi
if pgrep -x rtl_fm >/dev/null 2>&1; then
  echo "Stopping leftover rtl_fm..."
  pkill -x rtl_fm || true
  sleep 1
fi

mkdir -p "$(dirname "$UNIT_DST")"
install -m 0644 "$UNIT_SRC" "$UNIT_DST"
systemctl --user daemon-reload
systemctl --user enable --now nwr-alerts.service

# Boot without an interactive login
if command -v loginctl >/dev/null; then
  if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q 'Linger=yes'; then
    echo "Enabling lingering for $USER (service starts at boot without login)..."
    loginctl enable-linger "$USER" || sudo loginctl enable-linger "$USER"
  else
    echo "Linger already enabled for $USER"
  fi
fi

echo
echo "Installed: $UNIT_DST"
echo "Status:    systemctl --user status nwr-alerts.service"
echo "Logs:      journalctl --user -u nwr-alerts.service -f"
echo "Stop:      systemctl --user stop nwr-alerts.service"
echo "Disable:   systemctl --user disable --now nwr-alerts.service"
systemctl --user --no-pager --full status nwr-alerts.service || true
