# Zorin OS Power Tweaks — CPU Power

GTK 3 tray and control window for **Zorin OS** / GNOME on laptops where you want **auto-cpufreq**, Samsung-style **platform profiles**, **turbo**, **battery charge limit**, and related tweaks **without** relying on `power-profiles-daemon` for CPU policy.

Developed primarily for **Samsung Galaxy Book** (firmware `platform_profile`) and similar machines; some features degrade gracefully if hardware paths are missing.

**Repository:** [Zorin-Os-Power-Tweaks](https://github.com/Code-fever1/Zorin-Os-Power-Tweaks)

---

## Features

| Area | What it does |
|------|----------------|
| **Dashboard** | Quick overview: profile, turbo state, charge limit, CPU chart, memory strip |
| **CPU** | Samsung **Silent / Quiet / Balance / Power** and **Adaptive**; adaptive **charger + battery rules** (same options as before the Rules page merge); **turbo** (auto / off / on) |
| **Battery** | Charge limit (50–100%); **panel battery icon colors** per profile + optional **turbo** tint on the percentage while turbo is on (GNOME Shell extension) |
| **Display · Beta** | Optional **smooth adaptive screen brightness** user service; ties into GNOME “Automatic brightness”; fade and sensor responsiveness sliders |
| **Keyboard** | Keyboard **backlight** level and idle timeout; optional **ambient-linked** mode (dims or hides backlight when the room is bright, restores in the dark) using the ambient sensor |
| **Memory** | Swappiness; **zram-tools** integration (size, algorithm, boot toggle) when installed |
| **Scheduler** | Time-based overrides for charger/battery rules |
| **Monitor** | Live stats view |
| **Settings** | Save your **login password** in the keyring so privileged changes avoid repeating the full PAM fingerprint stack on every click; install/repair **auto-cpufreq** daemon |

---

## Requirements

- **Zorin OS** (or another GNOME-based desktop with similar session APIs).
- **Python 3** with **PyGObject** (`python3-gi`, `python3-gi-cairo`, GTK 3).
- **auto-cpufreq** installed and its **daemon** running for profile/turbo commands to apply reliably.
- **Tray icon** (optional): `gir1.2-ayatanaappindicator3-0.1`.
- **Saved password** (optional): **GNOME Keyring** / libsecret (schema `com.zorin.cpu-power.AdminPassword`).
- **zram-tools** (optional): only for the Memory page zram controls.
- **Ambient / display extras** (optional): `iio-sensor-proxy`, working backlight under `/sys/class/backlight`.

---

## Quick install

```bash
git clone https://github.com/Code-fever1/Zorin-Os-Power-Tweaks.git
cd Zorin-Os-Power-Tweaks
./install.sh
```

- Installs the app under `~/.local/share/zorin-cpu-power/`, symlink `~/.local/bin/zorin-cpu-power`, desktop entry, autostart tray entry, and copies the **battery color** Shell extension into `~/.local/share/gnome-shell/extensions/`.

**Tray icon dependency** (if you want the top-bar icon):

```bash
sudo apt install gir1.2-ayatanaappindicator3-0.1
```

**System helper + PolicyKit** (recommended: fewer prompts, whitelisted operations):

```bash
pkexec ./install-system-helper.sh
```

This installs `cpu-power-helper` as `/usr/local/libexec/zorin-cpu-power-helper`, Polkit action `com.zorin.cpu-power`, and a small **sudo/PAM** profile so saved-password applies use **password-only** authentication for that helper (avoids long waits when fingerprint PAM runs before password).

**Smooth adaptive brightness** (optional, beta): use `smooth-adaptive-brightness/install.sh` from this repo (see [smooth-adaptive-brightness/README.md](smooth-adaptive-brightness/README.md)).

---

## Usage

- Launch **CPU Power** from the app menu, or:  
  `zorin-cpu-power --window`  
- **Tray only:**  
  `zorin-cpu-power --tray-only`  
- The app is a **single instance** (`com.zorin.cpu-power`). After updating Python, **fully quit** the old process and start again so the UI reloads.

In **Settings → Login password**, store your password once; subsequent applies use `sudo` + the helper instead of opening `pkexec` every time (still protected by Polkit/sudo policy).

---

## Security model

- Privileged work goes through **`/usr/local/libexec/zorin-cpu-power-helper`**, which only allows a fixed set of operations (auto-cpufreq args, Samsung `platform_profile`, charge limit, swappiness, zram unit, etc.).
- **Polkit** policy is in `com.zorin.cpu-power.policy`; `install-system-helper.sh` registers it system-wide.
- Do not commit real passwords or keyring contents; config under `~/.config/zorin-cpu-power/` is user-local.

---

## GNOME Shell extension (battery colors)

Extension UUID: `cpu-power-battery-color@zorin.cpu-power`.  
`install.sh` copies it and adjusts `gsettings` so it can load after login. If the Shell session started **before** the extension existed, you may need a **session restart** once so GNOME picks up new extensions (normal GNOME limitation).

---

## Troubleshooting

| Symptom | What to check |
|--------|----------------|
| Profile/turbo errors | `systemctl status auto-cpufreq` — use **Repair daemon** in Settings or `auto-cpufreq --install` |
| Long delay on every change | Run `pkexec ./install-system-helper.sh` and save password in Settings |
| Tray missing | Install Ayatana AppIndicator package; ensure the app indicator extension is enabled in Zorin |
| Samsung modes missing | `/sys/firmware/acpi/platform_profile` must exist and list expected profiles |
| Keyboard ambient mode idle | Requires `net.hadess.SensorProxy` and ambient light support |

For **screen auto brightness** behavior and follow-ups, see [issue #1](https://github.com/Code-fever1/Zorin-Os-Power-Tweaks/issues/1).

---

## Development

- Main application: `zorin_cpu_power.py` (GTK 3).
- Privileged helper: `cpu-power-helper`.
- Polkit: `com.zorin.cpu-power.policy`.
- PAM/sudo drop-ins for the helper: `system/pam-zorin-cpu-power`, `system/sudoers-zorin-cpu-power` (installed by `install-system-helper.sh`).

```bash
python3 -m py_compile zorin_cpu_power.py
```

---

## License

Distributed under the [MIT License](LICENSE). You may change the copyright line in `LICENSE` to match your legal name or organization before publishing.

---

## Disclaimer

This is **user-maintained** tooling. It can change CPU frequencies, firmware platform profiles, charge limits, and related kernel settings. Use at your own risk on hardware you understand. Not affiliated with Zorin Group or Samsung.
