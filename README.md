# CPU Power (Zorin)

Small tray + window app to switch **auto-cpufreq** profiles without using the stock Settings power daemon for CPU control.

| Mode | Action |
|------|--------|
| **Adaptive (auto)** | `auto-cpufreq --force=reset` — daemon picks charger/battery rules |
| **Power saver / Performance** | `--force=powersave` / `--force=performance` |
| **Turbo** | `--turbo=auto`, `never`, or `always` (including on AC) |
| **Charge limit** | 50–100% presets (same as your `battery-limit` / `battery-full` aliases) |
| **Memory** | Swappiness; zram via **zram-tools** (size %, algorithm, boot toggle) |

## Install

```bash
cd ~/Projects/zorin-cpu-power
./install.sh
sudo apt install gir1.2-ayatanaappindicator3-0.1   # tray icon in top bar
```

Then run **CPU Power** from the app menu, or log out/in so autostart adds the tray icon.

## Notes

- Each change may ask for your password (`pkexec`).
- **Settings → Power** still uses `power-profiles-daemon` unless you disable it. This app is separate on purpose.
- For best results, enable the auto-cpufreq service: `systemctl start auto-cpufreq`.
