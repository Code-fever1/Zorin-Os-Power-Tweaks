#!/usr/bin/env python3
"""Smooth adaptive screen brightness for GNOME / Zorin."""

from __future__ import annotations

import json
import math
import signal
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

CONF_PATH = Path.home() / ".config" / "smooth-adaptive-brightness" / "config.json"
DEFAULTS = {
    "ambient_bandwidth_hz": 0.035,
    "brightness_time_constant_sec": 6.0,
    "tick_ms": 100,
    "backlight_name": "intel_backlight",
    "manual_pause_sec": 12.0,
    "auto_min_pct": 8.0,
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        data = json.loads(CONF_PATH.read_text())
        if isinstance(data, dict):
            for key in cfg:
                if key in data:
                    cfg[key] = data[key]
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    try:
        cfg["ambient_bandwidth_hz"] = float(cfg["ambient_bandwidth_hz"])
        cfg["brightness_time_constant_sec"] = float(cfg["brightness_time_constant_sec"])
        cfg["tick_ms"] = int(cfg["tick_ms"])
        cfg["manual_pause_sec"] = float(cfg["manual_pause_sec"])
        cfg["auto_min_pct"] = float(cfg["auto_min_pct"])
    except (TypeError, ValueError):
        cfg = dict(DEFAULTS)
    cfg["tick_ms"] = max(50, min(500, int(cfg["tick_ms"])))
    return cfg


def find_backlight() -> tuple[str, Path, int] | None:
    root = Path("/sys/class/backlight")
    if not root.is_dir():
        return None
    prefer = ("intel_backlight", "amdgpu_bl0", "acpi_video0")
    paths = {p.name: p for p in root.iterdir() if p.is_dir()}
    for name in prefer:
        if name in paths:
            path = paths[name]
            try:
                maximum = int((path / "max_brightness").read_text().strip())
            except (OSError, ValueError):
                continue
            if maximum > 0:
                return name, path, maximum
    for name, path in sorted(paths.items()):
        try:
            maximum = int((path / "max_brightness").read_text().strip())
        except (OSError, ValueError):
            continue
        if maximum > 0:
            return name, path, maximum
    return None


class SmoothAdaptiveBrightness:
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.loop = GLib.MainLoop()
        self.active = False
        self.claimed = False
        self.backlight_name = cfg["backlight_name"]
        self.backlight_path: Path | None = None
        self.backlight_max = 400
        self.norm_value = 1.0
        self.light_filtered = -1.0
        self.target_pct = -1.0
        self.shown_pct = -1.0
        self.last_good_light = 0.0
        self.last_light_time = 0.0
        self.last_tick = 0.0
        self.last_light_poll = 0.0
        self.write_hold_until = 0.0
        self.manual_hold_until = 0.0
        self._session_proxy = None
        self._sensor_proxy = None
        self._sensor_sub = 0
        self._tick_id = 0
        self._conf_mtime = 0.0
        self._settings = Gio.Settings.new("org.gnome.settings-daemon.plugins.power")

        found = find_backlight()
        if found:
            self.backlight_name, self.backlight_path, self.backlight_max = found

        self._settings.connect("changed::ambient-enabled", self._on_ambient_setting_changed)
        self._apply_ambient_setting(self._settings.get_boolean("ambient-enabled"))

    def _auto_min(self) -> float:
        return max(0.0, min(40.0, float(self.cfg.get("auto_min_pct", 8.0))))

    def _maybe_reload_config(self) -> None:
        try:
            mtime = CONF_PATH.stat().st_mtime
        except OSError:
            return
        if mtime == self._conf_mtime:
            return
        self._conf_mtime = mtime
        self.cfg = load_config()
        if self._tick_id and self.active:
            GLib.source_remove(self._tick_id)
            self._tick_id = GLib.timeout_add(int(self.cfg["tick_ms"]), self._tick)

    def _session(self):
        if self._session_proxy is None:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._session_proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.NONE,
                None,
                "org.freedesktop.login1",
                "/org/freedesktop/login1/session/auto",
                "org.freedesktop.login1.Session",
                None,
            )
        return self._session_proxy

    def _sensor(self):
        if self._sensor_proxy is None:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._sensor_proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.NONE,
                None,
                "net.hadess.SensorProxy",
                "/net/hadess/SensorProxy",
                "net.hadess.SensorProxy",
                None,
            )
        return self._sensor_proxy

    def _read_brightness_pct(self) -> float:
        if self.backlight_path is None:
            return 0.0
        try:
            raw = int((self.backlight_path / "brightness").read_text().strip())
        except (OSError, ValueError):
            return 0.0
        return max(0.0, min(100.0, raw * 100.0 / self.backlight_max))

    def _write_brightness_pct(self, pct: float) -> bool:
        pct = max(0.0, min(100.0, pct))
        if self.active and time.monotonic() >= self.manual_hold_until:
            pct = max(self._auto_min(), pct)
        raw = max(0, min(self.backlight_max, int(round(pct * self.backlight_max / 100.0))))
        try:
            self._session().call_sync(
                "SetBrightness",
                GLib.Variant("(ssu)", ("backlight", self.backlight_name, raw)),
                Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
            self.write_hold_until = time.monotonic() + 0.8
            return True
        except GLib.Error:
            self._session_proxy = None
            return False

    def _claim_light(self) -> bool:
        if self.claimed:
            return True
        try:
            self._sensor().call_sync("ClaimLight", None, Gio.DBusCallFlags.NONE, 5000, None)
            self.claimed = True
            return True
        except GLib.Error as exc:
            print(f"Could not claim ambient light sensor: {exc.message}", file=sys.stderr)
            self._sensor_proxy = None
            return False

    def _release_light(self) -> None:
        if not self.claimed:
            return
        try:
            self._sensor().call_sync("ReleaseLight", None, Gio.DBusCallFlags.NONE, 5000, None)
        except GLib.Error:
            pass
        self.claimed = False

    def _on_ambient_setting_changed(self, _settings, _key) -> None:
        self._apply_ambient_setting(self._settings.get_boolean("ambient-enabled"))

    def _calibrate(self, brightness_pct: float, light: float) -> None:
        brightness_pct = max(0.0, min(100.0, brightness_pct))
        if light > 0.05 and brightness_pct > 0.5:
            self.norm_value = light * 100.0 / brightness_pct
            self.last_good_light = light
        elif self.last_good_light > 0:
            pass
        else:
            self.norm_value = max(light, 0.2)
        self.shown_pct = brightness_pct
        self.target_pct = brightness_pct
        self.light_filtered = brightness_pct

    def _apply_ambient_setting(self, enabled: bool) -> None:
        if enabled and not self.active:
            if self.backlight_path is None:
                print("No backlight device found.", file=sys.stderr)
                return
            if not self._claim_light():
                return
            current = max(self._read_brightness_pct(), self._auto_min())
            light = self._read_light_level()
            self._calibrate(current, light)
            self.last_light_time = time.monotonic()
            self.last_tick = self.last_light_time
            self.last_light_poll = 0.0
            if self._sensor_sub == 0:
                self._sensor_sub = self._sensor().connect(
                    "g-properties-changed", self._on_sensor_properties_changed
                )
            if self._tick_id == 0:
                self._tick_id = GLib.timeout_add(int(self.cfg["tick_ms"]), self._tick)
            self.active = True
            self._update_light_target(self._read_light_level())
            print("Smooth adaptive brightness active.", flush=True)
        elif not enabled and self.active:
            self.active = False
            if self._tick_id:
                GLib.source_remove(self._tick_id)
                self._tick_id = 0
            if self._sensor_sub:
                try:
                    self._sensor().disconnect(self._sensor_sub)
                except (TypeError, RuntimeError):
                    pass
                self._sensor_sub = 0
            self._release_light()
            print("Smooth adaptive brightness idle.", flush=True)

    def _read_light_level(self) -> float:
        try:
            proxy = self._sensor()
            has = proxy.get_cached_property("HasAmbientLight")
            if has is None or not has.unpack():
                return self.last_good_light
            level = proxy.get_cached_property("LightLevel")
            if level is None:
                return self.last_good_light
            return max(0.0, float(level.unpack()))
        except (GLib.Error, TypeError, ValueError):
            return self.last_good_light

    def _on_sensor_properties_changed(self, _proxy, changed, _invalidated) -> None:
        if changed is None:
            return
        try:
            if changed.lookup_value("LightLevel", None) is None:
                return
        except (TypeError, KeyError, AttributeError):
            return
        self._update_light_target(self._read_light_level())

    def _on_manual_brightness(self, pct: float) -> None:
        if pct <= self._auto_min() + 1 and self.shown_pct > self._auto_min() + 5:
            return
        pause = float(self.cfg.get("manual_pause_sec", 12.0))
        self.manual_hold_until = time.monotonic() + pause
        light = self._read_light_level()
        self._calibrate(pct, light)
        print(f"Manual brightness {pct:.0f}% — auto paused {pause:.0f}s", flush=True)

    def _update_light_target(self, light: float) -> None:
        if not self.active or self.norm_value <= 0:
            return
        if light <= 0.05:
            return
        self.last_good_light = light
        now = time.monotonic()
        dt = max(0.001, now - self.last_light_time) if self.last_light_time else 1.0
        self.last_light_time = now
        bandwidth = max(0.005, float(self.cfg["ambient_bandwidth_hz"]))
        tau = 1.0 / (2.0 * math.pi * bandwidth)
        alpha = 1.0 / (1.0 + tau / dt)
        instant_pct = max(0.0, min(100.0, light * 100.0 / self.norm_value))
        floor = self._auto_min()
        instant_pct = max(floor, instant_pct)
        if self.light_filtered < 0:
            self.light_filtered = max(instant_pct, self.shown_pct, floor)
        else:
            blended = alpha * instant_pct + (1.0 - alpha) * self.light_filtered
            if blended < self.light_filtered:
                max_drop = 2.5 * dt
                self.light_filtered = max(blended, self.light_filtered - max_drop, floor)
            else:
                self.light_filtered = blended
        self.target_pct = max(floor, self.light_filtered)

    def _tick(self) -> bool:
        if not self.active:
            return False
        self._maybe_reload_config()
        now = time.monotonic()

        if now - self.last_light_poll >= 0.4:
            self.last_light_poll = now
            self._update_light_target(self._read_light_level())

        current_hw = self._read_brightness_pct()

        if now < self.manual_hold_until:
            return True

        if now >= self.write_hold_until:
            if current_hw <= self._auto_min() + 1 and self.shown_pct > self._auto_min() + 6:
                self._write_brightness_pct(self.shown_pct)
                return True
            if current_hw > self.shown_pct + 5:
                self._on_manual_brightness(current_hw)
                return True
            if current_hw + 5 < self.shown_pct and current_hw > self._auto_min() + 2:
                self._on_manual_brightness(current_hw)
                return True

        dt = max(0.001, now - self.last_tick) if self.last_tick else 0.1
        self.last_tick = now
        if self.target_pct < 0:
            self.target_pct = max(current_hw, self._auto_min())
            self.shown_pct = self.target_pct
        floor = self._auto_min()
        self.target_pct = max(floor, self.target_pct)
        tau = max(0.5, float(self.cfg["brightness_time_constant_sec"]))
        step = 1.0 - math.exp(-dt / tau)
        next_shown = self.shown_pct + (self.target_pct - self.shown_pct) * step
        if next_shown < self.shown_pct:
            max_drop = 3.0 * dt
            next_shown = max(next_shown, self.shown_pct - max_drop, floor)
        self.shown_pct = next_shown
        if abs(self.target_pct - self.shown_pct) < 0.2:
            self.shown_pct = self.target_pct
        if abs(self.shown_pct - current_hw) >= 0.5:
            self._write_brightness_pct(self.shown_pct)
        return True

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.loop.quit())
        signal.signal(signal.SIGINT, lambda *_: self.loop.quit())
        self.loop.run()
        self._apply_ambient_setting(False)


def main() -> int:
    cfg = load_config()
    if find_backlight() is None:
        print("No /sys/class/backlight device found.", file=sys.stderr)
        return 1
    SmoothAdaptiveBrightness(cfg).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
