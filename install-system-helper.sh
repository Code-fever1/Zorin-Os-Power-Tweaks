#!/usr/bin/env bash
# Install system helper + PolicyKit (run once with: pkexec ./install-system-helper.sh)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
install -m 755 "$ROOT/cpu-power-helper" /usr/local/libexec/zorin-cpu-power-helper
install -m 644 "$ROOT/com.zorin.cpu-power.policy" /usr/share/polkit-1/actions/com.zorin.cpu-power.policy
install -m 644 "$ROOT/system/pam-zorin-cpu-power" /etc/pam.d/zorin-cpu-power
install -m 440 "$ROOT/system/sudoers-zorin-cpu-power" /etc/sudoers.d/zorin-cpu-power
visudo -cf /etc/sudoers.d/zorin-cpu-power
echo "Installed /usr/local/libexec/zorin-cpu-power-helper and polkit policy."
echo "Saved-password changes use a password-only login, so the fingerprint reader is not asked."
echo "First change may ask for your password; later changes should reuse it (auth_admin_keep)."
