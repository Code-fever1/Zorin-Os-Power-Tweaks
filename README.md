# CPU Power (Zorin OS Power Tweaks)

**Desktop energy control for Zorin OS and GNOME** — one tray app for profiles, turbo, battery limits, brightness, memory tuning, and scheduling, built around **auto-cpufreq** and Samsung-style **platform profiles** instead of `power-profiles-daemon`.

| | |
|---|---|
| **Platform** | Zorin OS / GNOME (Wayland) |
| **Primary hardware** | Samsung Galaxy Book and similar laptops with ACPI `platform_profile` |
| **License** | [MIT](LICENSE) |
| **Repository** | [Code-fever1/Zorin-Os-Power-Tweaks](https://github.com/Code-fever1/Zorin-Os-Power-Tweaks) |

---

## Overview

CPU Power is a **GTK 3** application with a **system tray** and full settings window. It wraps **auto-cpufreq** for governor and turbo policy, maps **Silent / Quiet / Balance / Power / Adaptive** modes to firmware platform profiles where available, and adds laptop-specific controls (charge cap, panel battery colors, smooth adaptive brightness, keyboard backlight, zram/swappiness) in one place.

The design goal is **predictable behavior on real hardware**: fewer surprise turbo toggles, profile applies that do not overwrite your turbo choice, and privileged operations that stay inside a small, auditable helper.

---

## Highlights

- **Unified dashboard** — live CPU chart, profile, turbo state (including *Auto · On/Off*), charge limit, and memory snapshot  
- **CPU page** — Samsung platform modes, **adaptive charger/battery rules**, and turbo (**auto / off / on**)  
- **Battery** — configurable charge limit (50–100%) and **GNOME Shell** battery icon colors per profile (optional turbo tint on the percentage while boost is active)  
- **Display (beta)** — optional **smooth adaptive brightness** user service with fade and sensor tuning  
- **Keyboard** — backlight level, idle blanking, ambient-linked dimming (when a light sensor is available)  
- **Memory** — swappiness and **zram-tools** integration when installed  
- **Scheduler** — time-based overrides for charger/battery rules  
- **Fast applies** — optional login password in GNOME Keyring + dedicated **PAM** stack for the helper (avoids long fingerprint-first sudo delays)  
- **Stable auto turbo** — optional patch script for auto-cpufreq hysteresis so boost does not pulse around a single load threshold  

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│  zorin_cpu_power.py (GTK 3) — tray + ControlWindow          │
│  ~/.config/zorin-cpu-power/  ·  battery-live.json (tray)   │
└──────────────────────────┬──────────────────────────────────┘
                           │ pkexec / sudo → helper
                           ▼
┌─────────────────────────────────────────────────────────────┐
│  zorin-cpu-power-helper  — whitelisted sysfs / auto-cpufreq   │
│  Polkit: com.zorin.cpu-power                                │
└──────────────────────────┬──────────────────────────────────┘
                           │
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
  platform_profile    auto-cpufreq       charge / zram
  intel_pstate        turbo pickle       swappiness
```

**GNOME Shell extension** (`cpu-power-battery-color@zorin.cpu-power`) reads saved colors and, when the app is running, fresh state from `battery-live.json` so the panel icon matches the UI.

---

## Requirements

- **OS:** Zorin OS or another **GNOME** session with similar APIs (tested on Wayland).
- **Runtime:** Python 3, **PyGObject** (`python3-gi`, `python3-gi-cairo`, GTK 3).
- **CPU policy:** [auto-cpufreq](https://github.com/AdnanHodzic/auto-cpufreq) installed with **`auto-cpufreq.service`** active.
- **Tray (optional):** `gir1.2-ayatanaappindicator3-0.1`.
- **Saved password (optional):** GNOME Keyring / libsecret (`com.zorin.cpu-power.AdminPassword`).
- **zram-tools (optional):** for Memory page controls.
- **Sensors (optional):** `iio-sensor-proxy` and a backlight under `/sys/class/backlight` for display/ambient features.

Samsung **Silent/Quiet/Balance/Power** need `/sys/firmware/acpi/platform_profile` with expected profile names.

---

## Installation

### User app (no root)

```bash
git clone https://github.com/Code-fever1/Zorin-Os-Power-Tweaks.git
cd Zorin-Os-Power-Tweaks
./install.sh
```

This installs under `~/.local/share/zorin-cpu-power/`, adds `~/.local/bin/zorin-cpu-power`, a desktop entry, autostart tray entry, and copies the battery-color Shell extension.

**Tray icon dependency:**

```bash
sudo apt install gir1.2-ayatanaappindicator3-0.1
```

### System helper (recommended)

Whitelisted privileged operations and quicker applies when a password is saved:

```bash
pkexec ./install-system-helper.sh
```

Installs:

- `/usr/local/libexec/zorin-cpu-power-helper`
- Polkit policy `com.zorin.cpu-power`
- PAM/sudo drop-ins (`system/pam-zorin-cpu-power`, `system/sudoers-zorin-cpu-power`) so helper invocations can use **password-only** auth

### Optional: smooth adaptive brightness

```bash
./smooth-adaptive-brightness/install.sh
```

See [smooth-adaptive-brightness/README.md](smooth-adaptive-brightness/README.md).

### Optional: calmer auto turbo (auto-cpufreq)

If turbo pulses at low load, run once as root after auto-cpufreq is installed (re-run after auto-cpufreq upgrades that replace the venv):

```bash
sudo python3 system/stabilize-auto-turbo.py
sudo systemctl restart auto-cpufreq
```

---

## Usage

| Command | Behavior |
|---------|----------|
| `zorin-cpu-power --window` | Open the control window |
| `zorin-cpu-power --tray-only` | Tray icon only (typical autostart) |

The app is **single-instance** (`Gtk.Application` id `com.zorin.cpu-power`). After updating Python, **quit the running app completely** and start again.

In **Settings → Login password**, store your password once. Subsequent applies use `sudo` + the helper instead of a full `pkexec` flow on every click (still governed by Polkit/sudo policy).

---

## Security model

- Privileged work is limited to **`zorin-cpu-power-helper`** — a fixed set of operations (auto-cpufreq CLI, `platform_profile`, charge limit, swappiness, zram unit, etc.).
- **Do not** commit real passwords or keyring exports; user config lives under `~/.config/zorin-cpu-power/`.
- Custom PAM for the helper is a **machine-local** convenience for Samsung/Zorin setups; it is not presented as a universal Linux hardening pattern.

---

## GNOME Shell extension

- **UUID:** `cpu-power-battery-color@zorin.cpu-power`
- `install.sh` enables it via `gsettings` where possible.
- If GNOME started before the extension was installed, **log out and back in once** so Shell loads the new extension (normal GNOME limitation).

---

## Troubleshooting

| Symptom | Check |
|--------|--------|
| Profile/turbo errors | `systemctl status auto-cpufreq` — use **Repair daemon** in Settings or `auto-cpufreq --install` |
| Long delay on every change | Run `pkexec ./install-system-helper.sh` and save password in Settings |
| Missing tray | Install Ayatana AppIndicator; enable app indicator support in Zorin/GNOME |
| Samsung modes missing | `cat /sys/firmware/acpi/platform_profile` and available choices |
| Tray color out of sync | Ensure the app is running (publishes `battery-live.json` every ~2s) |
| Screen auto brightness | Tracking and notes in [issue #1](https://github.com/Code-fever1/Zorin-Os-Power-Tweaks/issues/1) |

---

## Project layout

| Path | Purpose |
|------|---------|
| `zorin_cpu_power.py` | Main GTK 3 application |
| `cpu-power-helper` | Privileged helper (installed to `/usr/local/libexec/`) |
| `com.zorin.cpu-power.policy` | Polkit rules |
| `shell-extension/` | GNOME battery color extension |
| `smooth-adaptive-brightness/` | Optional brightness daemon |
| `system/` | PAM, sudoers templates, auto-cpufreq turbo patcher |
| `install.sh` / `install-system-helper.sh` | Installers |

```bash
python3 -m py_compile zorin_cpu_power.py
```

---

## Contributing

Issues and PRs are welcome on GitHub. For behavior changes that touch firmware or cpufreq, include hardware model and kernel/GNOME versions.

---

## License

Released under the [MIT License](LICENSE). Copyright (c) 2026 Alijah — update the copyright line in `LICENSE` if you fork for publication.

---

## Disclaimer

This is **community-maintained** tooling. It can change CPU frequencies, firmware platform profiles, charge limits, and related kernel settings. Use at your own risk on hardware you understand. Not affiliated with Zorin Group or Samsung.
