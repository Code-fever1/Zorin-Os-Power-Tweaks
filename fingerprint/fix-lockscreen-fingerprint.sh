#!/bin/sh
# One fingerprint client on the lock screen, and a short miss timeout.
# gdm-password was also starting pam_fprintd, so it stole the reader from
# gdm-fingerprint. The real match was then cancelled and the lock screen
# showed no success animation while it waited.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 1
fi

PW=/etc/pam.d/gdm-password
FP=/etc/pam.d/gdm-fingerprint

if [ ! -f "${PW}.orig-fp" ]; then
  cp -a "$PW" "${PW}.orig-fp"
fi

python3 - "$PW" << 'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text()
old = "@include common-auth\n"
new = """# Fingerprint stays on gdm-fingerprint only. Sharing the reader with this
# stack cancels a successful match and the lock screen never shows success.
auth\t[success=1 default=ignore]\tpam_unix.so nullok try_first_pass
auth\trequisite\t\t\tpam_deny.so
auth\trequired\t\t\tpam_permit.so
"""
if old not in text:
    if "gdm-fingerprint only" in text:
        sys.exit(0)
    sys.exit("gdm-password does not include common-auth; not changing it")
path.write_text(text.replace(old, new, 1))
PY

python3 - "$FP" << 'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text()
old = "auth\trequired\tpam_fprintd.so\n"
new = "auth\trequired\tpam_fprintd.so max-tries=2 timeout=10\n"
if old in text:
    path.write_text(text.replace(old, new, 1))
elif "pam_fprintd.so max-tries=2 timeout=10" not in text:
    sys.exit("gdm-fingerprint pam_fprintd line was not found")
PY

echo "Lock screen fingerprint uses one reader client and a 10s miss timeout."
