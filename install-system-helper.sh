#!/usr/bin/env bash
# Install system helper + PolicyKit (run once with: pkexec ./install-system-helper.sh)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install -m 755 "$ROOT/cpu-power-helper" /usr/local/libexec/zorin-cpu-power-helper
install -m 644 "$ROOT/com.zorin.cpu-power.policy" /usr/share/polkit-1/actions/com.zorin.cpu-power.policy
echo "Installed /usr/local/libexec/zorin-cpu-power-helper and polkit policy."
echo "First change may ask for your password; later changes should reuse it (auth_admin_keep)."
