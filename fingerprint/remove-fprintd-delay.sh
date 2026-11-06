#!/bin/sh
# Drop the 2 second startup wait. Settings times out before fprintd appears on the bus.
set -eu
if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 1
fi
rm -f /etc/systemd/system/fprintd.service.d/override.conf
rmdir /etc/systemd/system/fprintd.service.d 2>/dev/null || true
systemctl daemon-reload
systemctl reset-failed fprintd.service 2>/dev/null || true
echo "fprintd starts immediately again."
