#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
APP_DIR="${HOME}/.local/share/zorin-cpu-power"
DESKTOP_DIR="${HOME}/.local/share/applications"
AUTOSTART_DIR="${HOME}/.config/autostart"

mkdir -p "$BIN_DIR" "$APP_DIR" "$DESKTOP_DIR" "$AUTOSTART_DIR"
install -m 755 "$ROOT/zorin_cpu_power.py" "$APP_DIR/zorin_cpu_power.py"
if [ -d "$ROOT/smooth-adaptive-brightness" ]; then
  rm -rf "$APP_DIR/smooth-adaptive-brightness"
  cp -a "$ROOT/smooth-adaptive-brightness" "$APP_DIR/"
fi
if [ -f /usr/share/pixmaps/auto-cpufreq.png ]; then
  cp /usr/share/pixmaps/auto-cpufreq.png "$APP_DIR/icon.png"
fi
ln -sf "$APP_DIR/zorin_cpu_power.py" "$BIN_DIR/zorin-cpu-power"

cat >"$DESKTOP_DIR/zorin-cpu-power.desktop" <<EOF
[Desktop Entry]
Name=CPU Power
Comment=Turbo, profiles, charge limit (auto-cpufreq)
Exec=${BIN_DIR}/zorin-cpu-power --window
Icon=preferences-system-power-symbolic
Terminal=false
Type=Application
Categories=GNOME;GTK;Settings;Utility;
Keywords=CPU;Power;Performance;Battery;auto-cpufreq;
StartupNotify=true
EOF

cat >"$AUTOSTART_DIR/zorin-cpu-power.desktop" <<EOF
[Desktop Entry]
Name=CPU Power (tray)
Comment=Keep CPU Power tray icon in the top bar
Exec=${BIN_DIR}/zorin-cpu-power --tray-only
Icon=preferences-system-power-symbolic
Terminal=false
Type=Application
X-GNOME-Autostart-enabled=true
Hidden=false
EOF

if ! python3 -c "import gi; gi.require_version('AyatanaAppIndicator3','0.1')" 2>/dev/null; then
  echo ""
  echo "Tray icon needs Ayatana AppIndicator (Zorin supports this via the app indicator extension):"
  echo "  sudo apt install gir1.2-ayatanaappindicator3-0.1"
  echo ""
  echo "Without it, the app still opens as a normal window from the app menu."
fi

EXT_UUID="cpu-power-battery-color@zorin.cpu-power"
EXT_DST="${HOME}/.local/share/gnome-shell/extensions/${EXT_UUID}"
mkdir -p "$EXT_DST"
cp -a "$ROOT/shell-extension/." "$EXT_DST/"
python3 - "$EXT_UUID" <<'PY'
import ast
import subprocess
import sys

uuid, old = sys.argv[1], "battery-power-mode-indicator@nirmaladvani.github.com"

def get_strv(key):
    raw = subprocess.check_output(["gsettings", "get", "org.gnome.shell", key], text=True).strip()
    value = ast.literal_eval(raw)
    return list(value) if isinstance(value, (list, tuple)) else []

def set_strv(key, values):
    rendered = "[" + ", ".join("'" + item.replace("'", "") + "'" for item in values) + "]"
    subprocess.check_call(["gsettings", "set", "org.gnome.shell", key, rendered])

enabled = [item for item in get_strv("enabled-extensions") if item != old]
if uuid not in enabled:
    enabled.append(uuid)
set_strv("enabled-extensions", enabled)
disabled = [item for item in get_strv("disabled-extensions") if item != uuid]
if old not in disabled:
    disabled.append(old)
set_strv("disabled-extensions", disabled)
PY
gnome-extensions enable "$EXT_UUID" >/dev/null 2>&1 || true

update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo "Installed. Open **CPU Power** from the app menu (not auto-cpufreq)."
echo "If it was already open, quit it fully and launch again so the UI reloads."
echo "One-time (fewer password prompts): pkexec ${ROOT}/install-system-helper.sh"
echo "Tray at login: log out/in, or: zorin-cpu-power --tray-only"
