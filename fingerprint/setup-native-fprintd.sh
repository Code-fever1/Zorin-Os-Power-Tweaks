#!/bin/sh
# Use the system fprintd driver for the Egis match-on-chip reader.
# No userspace fingerprint library. The sensor stays suspended until a login asks for it.
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 1
fi

DEBIAN_FRONTEND=noninteractive pam-auth-update --enable fprintd

install -d /usr/local/sbin
cat > /usr/local/sbin/fprintd-sdcp-reset << 'EOF'
#!/bin/sh
# After resume, drop a stale secure-session claim only. Do not wake fprintd
# unless it is already running, so the reader can stay suspended.
set -eu
found=0
for claim in /var/lib/fprint/egismoc/*/sdcp-claim; do
  [ -f "$claim" ] || continue
  rm -f "$claim"
  found=1
done
if [ "$found" -eq 1 ] && systemctl is-active --quiet fprintd.service; then
  systemctl restart fprintd.service
fi
EOF
chmod 755 /usr/local/sbin/fprintd-sdcp-reset

cat > /etc/systemd/system/fprintd-sdcp-reset.service << 'EOF'
[Unit]
Description=Clear stale Egis fingerprint claim after resume
After=suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/fprintd-sdcp-reset

[Install]
WantedBy=suspend.target hibernate.target hybrid-sleep.target suspend-then-hibernate.target
EOF

systemctl daemon-reload
systemctl enable fprintd-sdcp-reset.service
echo "Fingerprint login is using the system reader."
