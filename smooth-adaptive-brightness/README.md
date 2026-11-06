# Smooth adaptive screen brightness

Separate from CPU Power. When **Settings → Power → Automatic brightness** is on, GNOME usually jumps the screen brightness immediately (especially when you first enable auto). This user service claims the ambient light sensor and ramps brightness smoothly instead.

## Install

```bash
./install.sh
```

Runs as a **user systemd service** (`smooth-adaptive-brightness.service`), starts at login, survives reboots.

## Use

1. Leave the service enabled (default after install).
2. Turn **Automatic brightness** on in Zorin/GNOME Settings → Power.
3. Brightness should ease up and down over several seconds instead of snapping.

Turn automatic brightness **off** in Settings to return to fully manual control.

## Tune

Edit `~/.config/smooth-adaptive-brightness/config.json`:

- `ambient_bandwidth_hz` — lower = slower reaction to room light changes (default `0.035`)
- `brightness_time_constant_sec` — how long the screen takes to reach a new level (default `6`)
- `tick_ms` — update interval in ms (default `100`)

Then:

```bash
systemctl --user restart smooth-adaptive-brightness.service
```

## Uninstall

```bash
systemctl --user disable --now smooth-adaptive-brightness.service
rm -f ~/.local/bin/smooth-adaptive-brightness \
  ~/.config/systemd/user/smooth-adaptive-brightness.service
systemctl --user daemon-reload
```
