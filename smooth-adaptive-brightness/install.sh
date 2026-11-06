#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
UNIT_DIR="${HOME}/.config/systemd/user"
CONF_DIR="${HOME}/.config/smooth-adaptive-brightness"

mkdir -p "$BIN_DIR" "$UNIT_DIR" "$CONF_DIR"
install -m 755 "$ROOT/smooth_adaptive_brightness.py" "$BIN_DIR/smooth-adaptive-brightness"
if [ ! -f "$CONF_DIR/config.json" ]; then
  cat >"$CONF_DIR/config.json" <<'EOF'
{
  "ambient_bandwidth_hz": 0.035,
  "brightness_time_constant_sec": 6.0,
  "tick_ms": 100
}
EOF
fi
install -m 644 "$ROOT/smooth-adaptive-brightness.service" "$UNIT_DIR/smooth-adaptive-brightness.service"
systemctl --user daemon-reload
systemctl --user enable --now smooth-adaptive-brightness.service
echo "Installed and started smooth-adaptive-brightness (user service)."
echo "Turn on automatic brightness in Settings → Power when you want smooth auto dimming."
echo "Tune speed in ${CONF_DIR}/config.json"
