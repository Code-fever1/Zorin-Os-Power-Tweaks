#!/usr/bin/env python3
"""Smooth adaptive screen brightness for GNOME / Zorin.

Learns your manual brightness at each ambient level, then mixes that curve
with live sensor data so auto and Control Panel sliders work together.
"""

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
PREF_PATH = Path.home() / ".config" / "smooth-adaptive-brightness" / "preferences.json"
DEFAULTS = {
    "ambient_bandwidth_hz": 0.035,
    "brightness_time_constant_sec": 6.0,
    "tick_ms": 100,
    "backlight_name": "intel_backlight",
    "manual_pause_sec": 15.0,
    "auto_min_pct": 8.0,
    "manual_deadband_pct": 6.0,
    "manual_confirm_ticks": 3,
    "manual_grace_sec": 10.0,
    "learn_manual": True,
    "preference_mix": 0.62,
    "lux_merge_distance": 12.0,
    "max_preference_anchors": 32,
    "target_hysteresis_pct": 4.0,
    "learn_min_lux": 3.0,
    "learn_debounce_sec": 0.55,
    "learn_min_interval_sec": 2.0,
    "self_write_ignore_sec": 2.5,
    "resume_lux_ratio": 0.22,
    "resume_lux_min": 6.0,
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    try:
        data = json.loads(CONF_PATH.read_text())
        if isinstance(data, dict):
            for key in DEFAULTS:
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
        cfg["manual_deadband_pct"] = float(cfg["manual_deadband_pct"])
        cfg["manual_confirm_ticks"] = int(cfg["manual_confirm_ticks"])
        cfg["manual_grace_sec"] = float(cfg["manual_grace_sec"])
        cfg["learn_manual"] = bool(cfg.get("learn_manual", True))
        cfg["preference_mix"] = float(cfg.get("preference_mix", DEFAULTS["preference_mix"]))
        cfg["lux_merge_distance"] = float(cfg.get("lux_merge_distance", DEFAULTS["lux_merge_distance"]))
        cfg["max_preference_anchors"] = int(cfg.get("max_preference_anchors", DEFAULTS["max_preference_anchors"]))
    except (TypeError, ValueError):
        cfg = dict(DEFAULTS)
    cfg["tick_ms"] = max(50, min(500, int(cfg["tick_ms"])))
    cfg["ambient_bandwidth_hz"] = max(0.005, min(0.2, float(cfg["ambient_bandwidth_hz"])))
    cfg["brightness_time_constant_sec"] = max(1.0, min(30.0, float(cfg["brightness_time_constant_sec"])))
    cfg["manual_deadband_pct"] = max(3.0, min(20.0, float(cfg["manual_deadband_pct"])))
    cfg["manual_confirm_ticks"] = max(1, min(15, int(cfg["manual_confirm_ticks"])))
    cfg["manual_grace_sec"] = max(1.0, min(30.0, float(cfg["manual_grace_sec"])))
    cfg["preference_mix"] = max(0.0, min(1.0, float(cfg["preference_mix"])))
    return cfg


def load_preference_anchors() -> list[dict]:
    try:
        data = json.loads(PREF_PATH.read_text())
        raw = data.get("anchors", [])
        out: list[dict] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            lux = float(item.get("lux", 0))
            pct = float(item.get("pct", 0))
            if lux > 0 and pct >= 0:
                out.append({"lux": lux, "pct": max(0.0, min(100.0, pct))})
        out.sort(key=lambda a: a["lux"])
        return out
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return []


def save_preference_anchors(anchors: list[dict]) -> None:
    PREF_PATH.parent.mkdir(parents=True, exist_ok=True)
    PREF_PATH.write_text(json.dumps({"version": 1, "anchors": anchors}, indent=2) + "\n")


def merge_anchor(anchors: list[dict], lux: float, pct: float, merge_lux: float, cap: int) -> list[dict]:
    anchors = [dict(a) for a in anchors]
    for a in anchors:
        if abs(a["lux"] - lux) <= merge_lux:
            a["lux"] = 0.65 * a["lux"] + 0.35 * lux
            a["pct"] = 0.35 * a["pct"] + 0.65 * pct
            return _trim_anchors(anchors, cap)
    anchors.append({"lux": lux, "pct": pct})
    anchors.sort(key=lambda a: a["lux"])
    return _trim_anchors(anchors, cap)


def _trim_anchors(anchors: list[dict], cap: int) -> list[dict]:
    if len(anchors) <= cap:
        return anchors
    if cap < 4:
        return anchors[-cap:]
    keep: list[dict] = [anchors[0], anchors[-1]]
    middle = anchors[1:-1]
    slots = cap - 2
    if not middle:
        return keep
    step = max(1, len(middle) // slots)
    for i in range(0, len(middle), step):
        if len(keep) >= cap - 1:
            break
        keep.append(middle[i])
    keep.sort(key=lambda a: a["lux"])
    return keep[:cap]


def interpolate_preference(anchors: list[dict], lux: float) -> float | None:
    if not anchors:
        return None
    if lux <= anchors[0]["lux"]:
        return float(anchors[0]["pct"])
    if lux >= anchors[-1]["lux"]:
        return float(anchors[-1]["pct"])
    for left, right in zip(anchors, anchors[1:]):
        if left["lux"] <= lux <= right["lux"]:
            span = right["lux"] - left["lux"]
            if span <= 1e-6:
                return float(right["pct"])
            t = (lux - left["lux"]) / span
            return left["pct"] + (right["pct"] - left["pct"]) * t
    return float(anchors[-1]["pct"])


def ambient_pct_from_norm(lux: float, norm_value: float, floor: float) -> float:
    if norm_value <= 0 or lux <= 0.05:
        return floor
    return max(floor, min(100.0, lux * 100.0 / norm_value))


def mix_ambient_and_preference(
    lux: float,
    norm_value: float,
    anchors: list[dict],
    mix: float,
    floor: float,
) -> float:
    ambient = ambient_pct_from_norm(lux, norm_value, floor)
    learned = interpolate_preference(anchors, lux)
    if learned is None:
        return ambient
    weight = max(0.0, min(1.0, mix))
    if len(anchors) < 2:
        weight = min(weight, 0.85)
    return max(floor, min(100.0, weight * learned + (1.0 - weight) * ambient))


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
        self.last_written_pct = -1.0
        self.last_hw_pct = -1.0
        self._last_shell_write = 0.0
        self._manual_streak = 0
        self.anchors: list[dict] = load_preference_anchors()
        self._session_proxy = None
        self._sensor_proxy = None
        self._sensor_sub = 0
        self._tick_id = 0
        self._conf_mtime = 0.0
        self._bl_monitor = None
        self._ignore_panel_until = 0.0
        self._last_learn_time = 0.0
        self._panel_learn_id = 0
        self._panel_learn_pct = -1.0
        self._lux_filtered = -1.0
        self._prev_hw_pct = -1.0
        self.user_locked = False
        self.lock_lux = 0.0
        self.lock_pct = -1.0
        self._settings = Gio.Settings.new("org.gnome.settings-daemon.plugins.power")

        found = find_backlight()
        if found:
            self.backlight_name, self.backlight_path, self.backlight_max = found

        self._settings.connect("changed::ambient-enabled", self._on_ambient_setting_changed)
        self._apply_ambient_setting(self._settings.get_boolean("ambient-enabled"))

    def _auto_min(self) -> float:
        return max(0.0, min(40.0, float(self.cfg.get("auto_min_pct", 8.0))))

    def _brightness_floor(self) -> float:
        floor = self._auto_min()
        if self.user_locked and self.lock_pct >= 0:
            return min(floor, self.lock_pct)
        return floor

    def _manual_deadband(self) -> float:
        return float(self.cfg.get("manual_deadband_pct", 6.0))

    def _preference_mix(self) -> float:
        return float(self.cfg.get("preference_mix", 0.62))

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
        if time.monotonic() >= self.manual_hold_until and not self.user_locked:
            pct = max(self._brightness_floor(), pct)
        raw = max(0, min(self.backlight_max, int(round(pct * self.backlight_max / 100.0))))
        try:
            self._session().call_sync(
                "SetBrightness",
                GLib.Variant("(ssu)", ("backlight", self.backlight_name, raw)),
                Gio.DBusCallFlags.NONE,
                5000,
                None,
            )
            hold = max(0.35, float(self.cfg["brightness_time_constant_sec"]) * 0.08)
            ignore = float(self.cfg.get("self_write_ignore_sec", DEFAULTS["self_write_ignore_sec"]))
            now = time.monotonic()
            self.write_hold_until = now + hold
            self._ignore_panel_until = now + max(hold, min(ignore, 0.9))
            self._last_shell_write = now
            self.last_written_pct = pct
            self.last_hw_pct = pct
            return True
        except GLib.Error:
            self._session_proxy = None
            return False

    def _watch_backlight_file(self) -> None:
        if self.backlight_path is None or self._bl_monitor is not None:
            return
        brightness_file = self.backlight_path / "brightness"
        try:
            gfile = Gio.File.new_for_path(str(brightness_file))
            self._bl_monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._bl_monitor.connect("changed", self._on_backlight_file_changed)
        except GLib.Error:
            self._bl_monitor = None

    def _unwatch_backlight_file(self) -> None:
        if self._bl_monitor:
            try:
                self._bl_monitor.cancel()
            except GLib.Error:
                pass
            self._bl_monitor = None

    def _schedule_panel_learn(self, pct: float) -> None:
        self._panel_learn_pct = pct
        if self._panel_learn_id:
            GLib.source_remove(self._panel_learn_id)
        debounce_ms = int(float(self.cfg.get("learn_debounce_sec", DEFAULTS["learn_debounce_sec"])) * 1000)
        self._panel_learn_id = GLib.timeout_add(max(200, debounce_ms), self._deferred_panel_learn)

    def _deferred_panel_learn(self) -> bool:
        self._panel_learn_id = 0
        if not self.active:
            return False
        now = time.monotonic()
        if now < self._ignore_panel_until:
            return False
        pct = self._read_brightness_pct()
        if abs(pct - self._panel_learn_pct) > 2.5:
            return False
        if self.last_written_pct >= 0 and abs(pct - self.last_written_pct) < 4.0 and now < self._ignore_panel_until + 0.5:
            return False
        self._learn_user_brightness(pct, from_panel=True)
        return False

    def _on_backlight_file_changed(self, _mon, _file, _other, _event) -> None:
        if not self.active:
            return
        now = time.monotonic()
        if now < self._ignore_panel_until or now < self.write_hold_until:
            return
        pct = self._read_brightness_pct()
        if self.lock_pct >= 0 and abs(pct - self.lock_pct) < 4.0:
            return
        if self.shown_pct >= 0 and abs(pct - self.shown_pct) < 4.0:
            return
        if self.last_written_pct >= 0 and abs(pct - self.last_written_pct) < 4.0:
            return
        self._schedule_panel_learn(pct)

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
        elif self.norm_value <= 0:
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
            self.anchors = load_preference_anchors()
            current = max(self._read_brightness_pct(), self._auto_min())
            light = self._read_light_level()
            self._calibrate(current, light)
            self.last_hw_pct = current
            self.last_light_time = time.monotonic()
            self.last_tick = self.last_light_time
            self.last_light_poll = 0.0
            self._manual_streak = 0
            self._watch_backlight_file()
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
            if self._panel_learn_id:
                GLib.source_remove(self._panel_learn_id)
                self._panel_learn_id = 0
            if self._tick_id:
                GLib.source_remove(self._tick_id)
                self._tick_id = 0
            if self._sensor_sub:
                try:
                    self._sensor().disconnect(self._sensor_sub)
                except (TypeError, RuntimeError):
                    pass
                self._sensor_sub = 0
            self._unwatch_backlight_file()
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

    def _on_sensor_properties_changed(self, _proxy, _changed, _invalidated) -> None:
        self._update_light_target(self._read_light_level())

    def _desired_pct_for_light(self, light: float) -> float:
        floor = self._auto_min()
        return mix_ambient_and_preference(
            light, self.norm_value, self.anchors, self._preference_mix(), floor
        )

    def _filtered_lux(self) -> float:
        raw = self._read_light_level()
        if raw <= 0.05:
            return self.last_good_light if self.last_good_light > 0 else 0.0
        if self._lux_filtered < 0:
            self._lux_filtered = raw
            return raw
        alpha = 0.22
        self._lux_filtered = alpha * raw + (1.0 - alpha) * self._lux_filtered
        return self._lux_filtered

    def _pin_user_level(self, pct: float, lux: float) -> None:
        """Hold this brightness until room light actually changes."""
        pct = max(0.0, min(100.0, pct))
        self.shown_pct = pct
        self.target_pct = pct
        self.light_filtered = pct
        self.lock_pct = pct
        self.last_hw_pct = pct
        self.user_locked = True
        if lux > 0.05:
            self.lock_lux = lux
            self.last_good_light = lux
            if pct > 0.5:
                self.norm_value = lux * 100.0 / pct

    def _lux_moved(self, lux: float) -> bool:
        if self.lock_lux <= 0.05 or lux <= 0.05:
            return False
        delta = abs(lux - self.lock_lux)
        ratio = float(self.cfg.get("resume_lux_ratio", DEFAULTS["resume_lux_ratio"]))
        minimum = float(self.cfg.get("resume_lux_min", DEFAULTS["resume_lux_min"]))
        need = max(minimum, self.lock_lux * ratio)
        return delta >= need

    def _learn_user_brightness(self, pct: float, *, from_panel: bool = False) -> None:
        pct = max(0.0, min(100.0, pct))
        now = time.monotonic()
        min_iv = float(self.cfg.get("learn_min_interval_sec", DEFAULTS["learn_min_interval_sec"]))
        lux = self._filtered_lux()
        if now - self._last_learn_time < min_iv and from_panel:
            self._pin_user_level(pct, lux if lux > 0.05 else self.lock_lux)
            return
        min_lux = float(self.cfg.get("learn_min_lux", DEFAULTS["learn_min_lux"]))
        if self.user_locked and self.lock_pct >= 0 and abs(self.lock_pct - pct) < 1.5:
            return
        self._last_learn_time = now
        self._manual_streak = 0
        self.manual_hold_until = now + float(self.cfg.get("manual_grace_sec", DEFAULTS["manual_grace_sec"]))
        self._pin_user_level(pct, lux if lux > 0.05 else self.last_good_light)
        if lux >= min_lux and pct > 0.5 and self.cfg.get("learn_manual", True):
            merge = float(self.cfg.get("lux_merge_distance", 12.0))
            cap = int(self.cfg.get("max_preference_anchors", 32))
            self.anchors = merge_anchor(self.anchors, lux, pct, merge, cap)
            save_preference_anchors(self.anchors)
        label = "panel" if from_panel else "manual"
        if lux >= min_lux:
            print(
                f"Holding {pct:.0f}% at ~{lux:.0f} lux ({label}) until the room light changes",
                flush=True,
            )
        else:
            print(f"Holding {pct:.0f}% ({label}) until ambient light is readable", flush=True)

    def _user_moved_brightness(self, current_hw: float, now: float) -> bool:
        if now < self.manual_hold_until or now < self.write_hold_until:
            self._manual_streak = 0
            return False
        if abs(self.shown_pct - self.target_pct) > 2.5:
            self._manual_streak = 0
            return False
        if abs(current_hw - self.shown_pct) < self._manual_deadband():
            self._manual_streak = 0
            return False
        if self.last_written_pct >= 0 and abs(current_hw - self.last_written_pct) < 3.5:
            self._manual_streak = 0
            return False
        self._manual_streak += 1
        need = int(self.cfg.get("manual_confirm_ticks", 3))
        return self._manual_streak >= need

    def _update_light_target(self, light: float) -> None:
        if not self.active or self.norm_value <= 0:
            return
        if light <= 0.05:
            return
        if self.user_locked and not self._lux_moved(light):
            return
        if self.user_locked and self._lux_moved(light):
            self.user_locked = False
            base = self.lock_pct if self.lock_pct >= 0 else self.shown_pct
            self.shown_pct = base
            self.light_filtered = base
            print(
                f"Room light changed (~{self.lock_lux:.0f} → ~{light:.0f} lux) — fading from {base:.0f}%",
                flush=True,
            )
            self.lock_pct = -1.0
        self.last_good_light = light
        now = time.monotonic()
        dt = max(0.001, now - self.last_light_time) if self.last_light_time else 1.0
        self.last_light_time = now
        bandwidth = max(0.005, float(self.cfg["ambient_bandwidth_hz"]))
        tau = 1.0 / (2.0 * math.pi * bandwidth)
        alpha = 1.0 / (1.0 + tau / dt)
        instant_pct = self._desired_pct_for_light(light)
        fade_tau = max(0.5, float(self.cfg["brightness_time_constant_sec"]))
        lux_rise = (100.0 / (3.2 * fade_tau)) * dt
        lux_drop = lux_rise * 0.85
        floor = self._brightness_floor()
        if self.light_filtered < 0:
            self.light_filtered = max(instant_pct, self.shown_pct, floor)
        else:
            blended = alpha * instant_pct + (1.0 - alpha) * self.light_filtered
            if blended < self.light_filtered:
                self.light_filtered = max(blended, self.light_filtered - lux_drop, floor)
            else:
                self.light_filtered = min(blended, self.light_filtered + lux_rise)
        new_target = max(floor, self.light_filtered)
        hyst = float(self.cfg.get("target_hysteresis_pct", DEFAULTS["target_hysteresis_pct"]))
        if self.target_pct >= 0 and abs(new_target - self.target_pct) < hyst:
            new_target = self.target_pct
        self.target_pct = new_target

    def _tick(self) -> bool:
        if not self.active:
            return False
        self._maybe_reload_config()
        now = time.monotonic()

        if now - self.last_light_poll >= 0.5:
            self.last_light_poll = now
            self._update_light_target(self._read_light_level())

        current_hw = self._read_brightness_pct()
        our_write = self.last_written_pct >= 0 and abs(current_hw - self.last_written_pct) < 3.0
        dragging = (
            self._prev_hw_pct >= 0
            and now >= self._ignore_panel_until
            and not our_write
            and abs(current_hw - self._prev_hw_pct) > 5.0
        )
        panel_diff = (
            not our_write
            and now >= self._ignore_panel_until
            and abs(current_hw - self.shown_pct) > self._manual_deadband()
        )
        user_moved = dragging or panel_diff
        self._prev_hw_pct = current_hw
        self.last_hw_pct = current_hw
        if user_moved:
            lux = self._filtered_lux()
            self._pin_user_level(current_hw, lux if lux > 0.05 else self.last_good_light)
            self._schedule_panel_learn(current_hw)
            return True

        if self.user_locked:
            return True

        if self._user_moved_brightness(current_hw, now):
            self._learn_user_brightness(current_hw, from_panel=False)
            return True

        dt = max(0.001, now - self.last_tick) if self.last_tick else 0.1
        self.last_tick = now
        if self.target_pct < 0:
            self.target_pct = max(current_hw, self._auto_min())
            self.shown_pct = self.target_pct
        floor = self._brightness_floor()
        self.target_pct = max(floor, self.target_pct)
        tau = max(0.5, float(self.cfg["brightness_time_constant_sec"]))
        step = 1.0 - math.exp(-dt / tau)
        next_shown = self.shown_pct + (self.target_pct - self.shown_pct) * step
        cap = (100.0 / (3.0 * tau)) * dt
        cap_drop = cap * 0.85
        if next_shown < self.shown_pct:
            next_shown = max(next_shown, self.shown_pct - cap_drop, floor)
        else:
            next_shown = min(next_shown, self.shown_pct + cap, 100.0)
        self.shown_pct = next_shown
        user_owning = abs(current_hw - self.shown_pct) > self._manual_deadband()
        tracking = self.last_written_pct < 0 or abs(current_hw - self.last_written_pct) < 3.5
        gap = now - self._last_shell_write
        due = gap >= 0.45 or abs(self.shown_pct - current_hw) >= 8.0
        if abs(self.shown_pct - current_hw) >= 0.8 and not user_owning and tracking and due:
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
