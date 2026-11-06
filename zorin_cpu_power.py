#!/usr/bin/env python3
"""CPU Power — tray + control UI for auto-cpufreq, turbo, and battery charge limit."""

from __future__ import annotations

import json
import math
import os
import pickle
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from pathlib import Path

import cairo

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

AUTO_CPUFREQ = Path("/usr/local/bin/auto-cpufreq")
if not AUTO_CPUFREQ.is_file():
    AUTO_CPUFREQ = Path("/usr/bin/auto-cpufreq")

OVERRIDE_FILE = Path("/opt/auto-cpufreq/override.pickle")
TURBO_OVERRIDE_FILE = Path("/opt/auto-cpufreq/turbo-override.pickle")
AUTO_CPUFREQ_CONF = Path("/etc/auto-cpufreq.conf")
BATTERY_SYSFS = Path("/sys/class/power_supply/BAT1/charge_control_end_threshold")
APP_ID = "com.zorin.cpu-power"
ICON_PATH = Path("/usr/share/pixmaps/auto-cpufreq.png")
LOCAL_ICON = Path.home() / ".local/share/zorin-cpu-power/icon.png"
TRAY_ICON = Path.home() / ".local/share/zorin-cpu-power/tray-icon.png"
USER_CONF = Path.home() / ".config/zorin-cpu-power/last-charge-limit"
SAMSUNG_MODE_FILE = Path.home() / ".config/zorin-cpu-power/samsung-mode"
KBD_CONF = Path.home() / ".config/zorin-cpu-power/keyboard.json"
SMOOTH_BRIGHTNESS_CONF = Path.home() / ".config/smooth-adaptive-brightness/config.json"
SMOOTH_BRIGHTNESS_BIN = Path.home() / ".local/bin/smooth-adaptive-brightness"
SMOOTH_BRIGHTNESS_DEFAULTS = {
    "ambient_bandwidth_hz": 0.035,
    "brightness_time_constant_sec": 6.0,
    "tick_ms": 100,
    "auto_min_pct": 8.0,
}
BATTERY_ICON_FILE = Path.home() / ".config/zorin-cpu-power/battery-icon.json"
BATTERY_ICON_MODES = (
    ("silent", "Silent", "#2DBA6E"),
    ("quiet", "Quiet", "#1FA7A0"),
    ("balance", "Balance", "#7EB8FF"),
    ("power", "Power", "#3484E4"),
)
TURBO_PERCENT = ("turbo", "Turbo", "#FFBE6F")
KBD_TIMEOUTS = (
    (0, "Always on"),
    (10, "10 sec"),
    (30, "30 sec"),
    (60, "1 min"),
    (180, "3 min"),
    (300, "5 min"),
)
PLATFORM_PROFILE = Path("/sys/firmware/acpi/platform_profile")
SAMSUNG_LABELS = {
    "adaptive": "Adaptive",
    "silent": "Silent",
    "quiet": "Quiet",
    "balance": "Balance",
    "power": "Power",
}
SAMSUNG_PLATFORM = {
    "silent": "low-power",
    "quiet": "quiet",
    "balance": "balanced",
    "power": "performance",
}
PRIV_HELPER = Path("/usr/local/libexec/zorin-cpu-power-helper")

GOVERNORS = ("performance", "powersave", "schedutil")
GOVERNOR_LABELS = {
    "performance": "Performance",
    "powersave": "Power saver",
    "schedutil": "Adaptive",
}
TURBO_MODES = ("auto", "never", "always")
TURBO_LABELS = {"auto": "Auto", "never": "Never", "always": "Always"}
_APPLY_LOCK = threading.Lock()

APP_CSS = """
window.cpubg {
  background-color: #0a0e1a;
}
.cpubg * {
  color: #e8ecf4;
}
.cpu-sidebar {
  background-color: rgba(12, 18, 36, 0.95);
  border-right: 1px solid rgba(80, 120, 255, 0.15);
}
.cpu-sidebar-title {
  font-weight: 700;
  font-size: 15px;
  color: #7eb8ff;
  padding: 8px 4px 16px 4px;
}
.cpu-nav-btn {
  background: transparent;
  border: none;
  border-radius: 10px;
  padding: 10px 12px;
  margin: 2px 0;
  color: #a8b4cc;
}
.cpu-nav-btn:hover {
  background: rgba(60, 100, 200, 0.18);
  color: #e8ecf4;
}
.cpu-nav-btn.active {
  background: linear-gradient(90deg, rgba(40, 120, 255, 0.35), rgba(80, 60, 220, 0.2));
  color: #ffffff;
  box-shadow: inset 3px 0 0 #3d8bff;
}
.cpu-sidebar-footer {
  color: #6a7894;
  font-size: 11px;
}
.cpu-main-scroll {
  background: transparent;
}
.cpu-content {
  padding: 20px 24px 12px 24px;
}
.cpu-hero-title {
  font-size: 26px;
  font-weight: 700;
  color: #ffffff;
}
.cpu-hero-sub {
  color: #8a96ae;
  font-size: 13px;
}
.cpu-card {
  background-color: rgba(18, 26, 48, 0.88);
  border: 1px solid rgba(70, 110, 220, 0.22);
  border-radius: 16px;
  padding: 18px 20px;
  margin-bottom: 14px;
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.35);
}
.cpu-card-title {
  font-size: 16px;
  font-weight: 700;
  color: #ffffff;
}
.cpu-card-desc {
  color: #8a96ae;
  font-size: 12px;
}
.cpu-beta-badge {
  font-size: 10px;
  font-weight: 700;
  color: #fbbf24;
  background-color: rgba(251, 191, 36, 0.12);
  border: 1px solid rgba(251, 191, 36, 0.35);
  border-radius: 6px;
  padding: 2px 8px;
}
.profile-tile {
  background: rgba(14, 20, 38, 0.9);
  border: 1px solid rgba(60, 90, 160, 0.35);
  border-radius: 14px;
  padding: 14px 10px;
  min-height: 120px;
}
.profile-tile:hover {
  border-color: rgba(80, 140, 255, 0.55);
  background: rgba(22, 32, 58, 0.95);
}
.profile-tile.selected {
  border: 2px solid #3d8bff;
  background: linear-gradient(180deg, rgba(35, 80, 180, 0.35), rgba(18, 26, 48, 0.95));
  box-shadow: 0 0 18px rgba(61, 139, 255, 0.35);
}
.profile-tile-title {
  font-weight: 700;
  font-size: 14px;
}
.profile-tile-desc {
  font-size: 11px;
  color: #8a96ae;
}
.profile-tile-icon {
  font-size: 22px;
}
.turbo-segment button {
  background: rgba(14, 20, 38, 0.9);
  border: 1px solid rgba(60, 90, 160, 0.35);
  border-radius: 10px;
  padding: 10px 24px;
  color: #a8b4cc;
  margin: 0 4px;
}
.turbo-segment button:hover {
  background: rgba(30, 45, 80, 0.95);
}
.turbo-segment button.selected {
  background: linear-gradient(180deg, #2a6fd4, #1e4a9a);
  color: #ffffff;
  border-color: #5aa0ff;
}
.turbo-auto-btn {
  background: rgba(14, 20, 38, 0.9);
  border: 1px solid rgba(60, 90, 160, 0.35);
  border-radius: 10px;
  padding: 10px 24px;
  color: #a8b4cc;
}
.turbo-auto-btn:hover {
  background: rgba(30, 45, 80, 0.95);
}
.turbo-auto-btn.selected {
  background: linear-gradient(180deg, #2a6fd4, #1e4a9a);
  color: #ffffff;
  border-color: #5aa0ff;
}
.turbo-manual-row {
  padding: 4px 0;
}
.turbo-manual-row.dimmed {
  opacity: 0.38;
}
.turbo-manual-label {
  color: #9aa8c0;
  font-size: 12px;
  min-width: 36px;
}
.cpu-form-label {
  color: #9aa8c0;
  font-size: 12px;
}
.cpu-combo {
  background: rgba(10, 16, 32, 0.95);
  border: 1px solid rgba(60, 90, 160, 0.4);
  border-radius: 8px;
  padding: 4px 8px;
  min-width: 180px;
}
.cpu-combo button {
  background: transparent;
  color: #e8ecf4;
}
.preset-btn {
  background: rgba(14, 20, 38, 0.9);
  border: 1px solid rgba(60, 90, 160, 0.35);
  border-radius: 999px;
  padding: 6px 14px;
  color: #a8b4cc;
}
.preset-btn:hover {
  border-color: #5aa0ff;
}
.preset-btn.selected {
  background: linear-gradient(180deg, #2a6fd4, #1e4a9a);
  color: #fff;
  border-color: #5aa0ff;
}
.cpu-apply-btn {
  background: linear-gradient(180deg, #2a6fd4, #1e4a9a);
  color: #ffffff;
  border: none;
  border-radius: 10px;
  padding: 8px 18px;
  font-weight: 600;
}
.cpu-apply-btn:hover {
  background: linear-gradient(180deg, #3d8bff, #2a6fd4);
}
.cpu-limit-active {
  color: #5ddea8;
  font-weight: 600;
}
.cpu-statusbar {
  background-color: rgba(12, 18, 36, 0.98);
  border-top: 1px solid rgba(70, 110, 220, 0.2);
  padding: 10px 20px;
}
.cpu-status-chip {
  color: #9aa8c0;
  font-size: 11px;
  margin-right: 18px;
}
.cpu-status-chip b {
  color: #e8ecf4;
  font-weight: 600;
}
.cpu-toast {
  color: #7eb8ff;
  font-size: 12px;
  padding: 4px 0 8px 0;
}
.cpu-daemon-banner {
  background-color: rgba(120, 40, 50, 0.45);
  border: 1px solid rgba(255, 120, 120, 0.45);
  border-radius: 12px;
  padding: 12px 16px;
  margin-bottom: 12px;
}
.cpu-daemon-banner-text {
  color: #ffb4b4;
  font-size: 12px;
}
.cpu-stat-value {
  font-size: 22px;
  font-weight: 700;
  color: #ffffff;
}
.cpu-quick {
  background: rgba(16, 24, 48, 0.95);
  border: 1px solid rgba(70, 110, 200, 0.35);
  border-radius: 14px;
  padding: 12px 8px;
  color: #d5def0;
}
.cpu-quick:hover {
  border-color: #5aa0ff;
}
.cpu-quick.selected {
  background: linear-gradient(180deg, #2a6fd4, #243a86);
  color: #ffffff;
  border-color: #7eb6ff;
}
.page-title {
  font-size: 26px;
  font-weight: 700;
  color: #ffffff;
}
.cpu-spin {
  background: rgba(10, 16, 32, 0.95);
  border: 1px solid rgba(60, 90, 160, 0.4);
  border-radius: 8px;
  padding: 4px;
}
window.tray-popup {
  background-color: transparent;
}
.tray-shell {
  background-color: transparent;
  padding: 14px 16px 22px 16px;
}
.tray-title {
  font-size: 15px;
  font-weight: 700;
  color: #f0f4ff;
}
.tray-status-ok {
  color: #4ade80;
  font-size: 12px;
}
.tray-status-bad {
  color: #f87171;
  font-size: 12px;
}
.tray-icon-btn {
  background: rgba(22, 30, 48, 0.96);
  border: 1px solid rgba(90, 120, 180, 0.28);
  border-radius: 12px;
  padding: 0;
  min-width: 36px;
  min-height: 36px;
}
.tray-icon-btn:hover {
  border-color: #5aa0ff;
  background: rgba(35, 55, 95, 0.95);
}
.tray-mid-card {
  background-color: rgba(12, 18, 32, 0.92);
  border: 1px solid rgba(55, 90, 160, 0.35);
  border-radius: 14px;
  padding: 12px;
}
.tray-stat {
  color: #dbe4f8;
  font-size: 13px;
  font-weight: 600;
}
.tray-profile-label {
  color: #8fa3c4;
  font-size: 11px;
  font-weight: 600;
}
.tray-profile-btn {
  background: rgba(18, 26, 44, 0.96);
  border: 1px solid rgba(70, 100, 160, 0.35);
  border-radius: 14px;
  padding: 0;
  min-width: 58px;
  min-height: 58px;
}
.tray-profile-btn:hover {
  border-color: #6aa8ff;
}
.tray-profile-btn.selected {
  background: linear-gradient(180deg, rgba(35, 95, 210, 0.55), rgba(18, 40, 95, 0.85));
  border-color: #6eb4ff;
  box-shadow: 0 0 14px rgba(70, 150, 255, 0.45);
  color: #ffffff;
}
.tray-profile-icon {
  font-size: 18px;
}
.tray-profile-name {
  font-size: 10px;
  color: #b8c6de;
}
.tray-bottom-card {
  background-color: rgba(12, 18, 32, 0.92);
  border: 1px solid rgba(55, 90, 160, 0.35);
  border-radius: 14px;
  padding: 10px 12px;
}
.tray-field-label {
  color: #8fa3c4;
  font-size: 11px;
  font-weight: 600;
}
.tray-combo {
  background: rgba(10, 16, 30, 0.95);
  border: 1px solid rgba(60, 95, 170, 0.45);
  border-radius: 10px;
  padding: 4px 8px;
  min-width: 120px;
}
.tray-combo button {
  background: transparent;
  color: #e8ecf4;
}
.tray-step-btn {
  background: rgba(18, 28, 50, 0.95);
  border: 1px solid rgba(60, 95, 170, 0.45);
  border-radius: 8px;
  padding: 2px 10px;
  min-width: 0;
}
switch.tray-switch {
  background: #3a445c;
  border-color: #3a445c;
  border-radius: 99px;
  min-width: 42px;
  min-height: 22px;
}
switch.tray-switch slider {
  background: #e8eef8;
  min-width: 16px;
  min-height: 16px;
}
switch.tray-switch:checked {
  background: #2f7bff;
  border-color: #2f7bff;
}
switch.tray-switch:checked slider {
  background: #ffffff;
}
progressbar.tray-charge-bar {
  min-height: 8px;
}
progressbar.tray-charge-bar trough {
  background: rgba(8, 12, 22, 1);
  border-radius: 6px;
  min-height: 8px;
}
progressbar.tray-charge-bar progress {
  background: linear-gradient(90deg, #16a34a, #4ade80);
  border-radius: 6px;
  min-height: 8px;
}
"""


def apply_app_theme() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(APP_CSS.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(),
        provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )


def _draw_lightning(cr, cx: float, cy: float, size: float) -> None:
    cr.move_to(cx + size * 0.12, cy - size * 0.52)
    cr.line_to(cx - size * 0.34, cy + size * 0.04)
    cr.line_to(cx - size * 0.04, cy + size * 0.04)
    cr.line_to(cx - size * 0.14, cy + size * 0.52)
    cr.line_to(cx + size * 0.36, cy - size * 0.06)
    cr.line_to(cx + size * 0.04, cy - size * 0.06)
    cr.close_path()


def ensure_tray_icon() -> Path:
    TRAY_ICON.parent.mkdir(parents=True, exist_ok=True)
    for size, path in ((24, TRAY_ICON), (48, LOCAL_ICON)):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        cr = cairo.Context(surface)
        cr.scale(size / 48.0, size / 48.0)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_source_rgba(0.9, 0.92, 0.96, 1)
        _draw_lightning(cr, 24, 24, 36)
        cr.fill()
        surface.write_to_png(str(path))
    return TRAY_ICON


class LineIcon(Gtk.DrawingArea):
    """Small line icon used in the tray popup."""

    def __init__(self, kind: str, size: int = 18) -> None:
        super().__init__()
        self.kind = kind
        self.set_size_request(size, size)
        self.connect("draw", self._draw)

    def _draw(self, _widget, cr) -> bool:
        selected = False
        parent = self.get_parent()
        if parent is not None and parent.get_style_context().has_class("selected"):
            selected = True
        color = (1.0, 1.0, 1.0) if selected else (0.62, 0.74, 0.92)
        paint_line_icon(cr, self.kind, self.get_allocated_width(), self.get_allocated_height(), color)
        return False


def paint_line_icon(cr, kind: str, w: float, h: float, color: tuple[float, float, float]) -> None:
    cr.save()
    cr.translate(w * 0.12, h * 0.12)
    s = min(w, h) * 0.76
    cr.set_source_rgb(*color)
    cr.set_line_width(max(1.4, s * 0.09))
    cr.set_line_cap(cairo.LINE_CAP_ROUND)
    cr.set_line_join(cairo.LINE_JOIN_ROUND)
    if kind == "bolt":
        cr.set_line_width(0)
        _draw_lightning(cr, s * 0.5, s * 0.52, s * 0.95)
        cr.fill()
    elif kind == "chart":
        cr.move_to(s * 0.15, s * 0.75)
        cr.line_to(s * 0.38, s * 0.48)
        cr.line_to(s * 0.55, s * 0.62)
        cr.line_to(s * 0.85, s * 0.22)
        cr.stroke()
    elif kind == "gear":
        cr.arc(s * 0.5, s * 0.5, s * 0.18, 0, 2 * math.pi)
        cr.stroke()
        cr.arc(s * 0.5, s * 0.5, s * 0.36, 0, 2 * math.pi)
        cr.stroke()
    elif kind == "close":
        cr.move_to(s * 0.25, s * 0.25)
        cr.line_to(s * 0.75, s * 0.75)
        cr.move_to(s * 0.75, s * 0.25)
        cr.line_to(s * 0.25, s * 0.75)
        cr.stroke()
    elif kind == "chip":
        cr.rectangle(s * 0.28, s * 0.28, s * 0.44, s * 0.44)
        cr.stroke()
        for t in (0.22, 0.42, 0.62):
            cr.move_to(s * t, s * 0.12)
            cr.line_to(s * t, s * 0.28)
            cr.move_to(s * t, s * 0.72)
            cr.line_to(s * t, s * 0.88)
            cr.move_to(s * 0.12, s * t)
            cr.line_to(s * 0.28, s * t)
            cr.move_to(s * 0.72, s * t)
            cr.line_to(s * 0.88, s * t)
        cr.stroke()
    elif kind == "thermo":
        cr.arc(s * 0.5, s * 0.72, s * 0.16, 0, 2 * math.pi)
        cr.stroke()
        cr.rectangle(s * 0.42, s * 0.16, s * 0.16, s * 0.48)
        cr.stroke()
    elif kind == "layers":
        cr.move_to(s * 0.5, s * 0.16)
        cr.line_to(s * 0.86, s * 0.36)
        cr.line_to(s * 0.5, s * 0.56)
        cr.line_to(s * 0.14, s * 0.36)
        cr.close_path()
        cr.stroke()
        cr.move_to(s * 0.14, s * 0.5)
        cr.line_to(s * 0.5, s * 0.7)
        cr.line_to(s * 0.86, s * 0.5)
        cr.move_to(s * 0.14, s * 0.66)
        cr.line_to(s * 0.5, s * 0.86)
        cr.line_to(s * 0.86, s * 0.66)
        cr.stroke()
    elif kind == "leaf":
        cr.move_to(s * 0.22, s * 0.78)
        cr.curve_to(s * 0.18, s * 0.3, s * 0.55, s * 0.12, s * 0.82, s * 0.22)
        cr.curve_to(s * 0.7, s * 0.7, s * 0.4, s * 0.82, s * 0.22, s * 0.78)
        cr.stroke()
    elif kind == "scales":
        cr.move_to(s * 0.5, s * 0.18)
        cr.line_to(s * 0.5, s * 0.78)
        cr.move_to(s * 0.28, s * 0.82)
        cr.line_to(s * 0.72, s * 0.82)
        cr.move_to(s * 0.18, s * 0.34)
        cr.line_to(s * 0.82, s * 0.34)
        cr.stroke()
        cr.arc(s * 0.28, s * 0.48, s * 0.12, math.pi, 0)
        cr.arc(s * 0.72, s * 0.48, s * 0.12, math.pi, 0)
        cr.stroke()
    elif kind == "gauge":
        cr.arc(s * 0.5, s * 0.58, s * 0.32, math.pi * 0.15, math.pi * 0.85)
        cr.stroke()
        cr.move_to(s * 0.5, s * 0.58)
        cr.line_to(s * 0.72, s * 0.36)
        cr.stroke()
    elif kind == "spark":
        cr.move_to(s * 0.5, s * 0.1)
        cr.line_to(s * 0.5, s * 0.9)
        cr.move_to(s * 0.1, s * 0.5)
        cr.line_to(s * 0.9, s * 0.5)
        cr.move_to(s * 0.22, s * 0.22)
        cr.line_to(s * 0.78, s * 0.78)
        cr.move_to(s * 0.78, s * 0.22)
        cr.line_to(s * 0.22, s * 0.78)
        cr.stroke()
    elif kind == "battery":
        cr.rectangle(s * 0.16, s * 0.28, s * 0.58, s * 0.44)
        cr.stroke()
        cr.rectangle(s * 0.74, s * 0.4, s * 0.1, s * 0.2)
        cr.fill()
    else:
        cr.arc(s * 0.5, s * 0.5, s * 0.2, 0, 2 * math.pi)
        cr.stroke()
    cr.restore()


class TrayCpuGauge(Gtk.DrawingArea):
    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(96, 96)
        self.value = 0.0
        self.shown = 0.0
        self.connect("draw", self._draw)
        GLib.timeout_add(33, self._tick)

    def set_value(self, pct: float) -> None:
        self.value = max(0.0, min(100.0, pct))

    def _tick(self) -> bool:
        self.shown += (self.value - self.shown) * 0.15
        if abs(self.value - self.shown) < 0.2:
            self.shown = self.value
        self.queue_draw()
        return True

    def _draw(self, _widget, cr) -> bool:
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        cx, cy = w / 2, h / 2
        radius = min(w, h) / 2 - 8
        cr.set_line_width(9)
        cr.set_source_rgba(0.1, 0.14, 0.24, 1)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()
        cr.set_source_rgb(0.28, 0.92, 0.52)
        start = -math.pi / 2
        cr.arc(cx, cy, radius, start, start + 2 * math.pi * (self.shown / 100.0))
        cr.stroke()
        cr.set_source_rgb(1, 1, 1)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(20)
        label = f"{self.shown:.0f}%"
        ext = cr.text_extents(label)
        cr.move_to(cx - ext.width / 2, cy + 2)
        cr.show_text(label)
        cr.set_source_rgb(0.65, 0.74, 0.88)
        cr.set_font_size(11)
        ext = cr.text_extents("CPU")
        cr.move_to(cx - ext.width / 2, cy + 18)
        cr.show_text("CPU")
        return False


TRAY_PROFILES = (
    ("leaf", "Eco", "silent"),
    ("scales", "Balanced", "balance"),
    ("gauge", "Performance", "power"),
    ("spark", "Turbo", "turbo"),
)


class TrayPopupWindow(Gtk.Window):
    def __init__(self, app: "CpuPowerApp") -> None:
        super().__init__(title="CPU Power")
        self.app = app
        self._updating = False
        self._profile_btns: dict[str, Gtk.Button] = {}
        self._tail_x = 330
        self._tail_down = True
        self.set_app_paintable(True)
        screen = self.get_screen()
        visual = screen.get_rgba_visual() if screen else None
        if visual is not None:
            self.set_visual(visual)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_default_size(680, 332)
        self.get_style_context().add_class("tray-popup")
        self.connect("draw", self._paint_chrome)
        self.connect("delete-event", lambda *_: self.hide() or True)
        self.connect("key-press-event", self._on_key)

        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        shell.get_style_context().add_class("tray-shell")
        self.add(shell)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        logo = Gtk.DrawingArea()
        logo.set_size_request(34, 34)
        logo.connect("draw", self._paint_logo)
        title = Gtk.Label(label="CPU Power")
        title.get_style_context().add_class("tray-title")
        self.status_lbl = Gtk.Label()
        self.status_lbl.set_markup("<span color='#3ddc84'>●</span>  <span color='#c5d0e4'>Running</span>")
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        title_row.pack_start(logo, False, False, 0)
        title_row.pack_start(title, False, False, 0)
        title_row.pack_start(self.status_lbl, False, False, 0)
        title_row.set_valign(Gtk.Align.CENTER)
        header.pack_start(title_row, True, True, 0)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for kind, tip, cb in (
            ("chart", "Monitor", self._open_monitor),
            ("gear", "Settings", self._open_settings),
            ("close", "Close", self.hide),
        ):
            btn = Gtk.Button()
            btn.set_tooltip_text(tip)
            btn.get_style_context().add_class("tray-icon-btn")
            btn.set_size_request(36, 36)
            btn.add(LineIcon(kind, 16))
            btn.connect("clicked", lambda _b, f=cb: f())
            actions.pack_start(btn, False, False, 0)
        header.pack_start(actions, False, False, 0)
        shell.pack_start(header, False, False, 0)

        mid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        mid.get_style_context().add_class("tray-mid-card")
        self.gauge = TrayCpuGauge()
        mid.pack_start(self.gauge, False, False, 0)
        stats = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        stats.set_valign(Gtk.Align.CENTER)
        self.stat_freq = Gtk.Label(halign=Gtk.Align.START)
        self.stat_temp = Gtk.Label(halign=Gtk.Align.START)
        self.stat_watts = Gtk.Label(halign=Gtk.Align.START)
        for kind, lbl in (("chip", self.stat_freq), ("thermo", self.stat_temp), ("bolt", self.stat_watts)):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            row.pack_start(LineIcon(kind, 16), False, False, 0)
            lbl.get_style_context().add_class("tray-stat")
            row.pack_start(lbl, False, False, 0)
            stats.pack_start(row, False, False, 0)
        mid.pack_start(stats, False, False, 0)
        prof_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        prof_box.set_hexpand(True)
        prof_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        prof_head.pack_start(LineIcon("layers", 14), False, False, 0)
        pl = Gtk.Label(label="Profile", halign=Gtk.Align.START)
        pl.get_style_context().add_class("tray-profile-label")
        prof_head.pack_start(pl, False, False, 0)
        prof_box.pack_start(prof_head, False, False, 0)
        prof_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        prof_row.set_homogeneous(True)
        for kind, name, mode in TRAY_PROFILES:
            btn = Gtk.Button()
            btn.set_tooltip_text(name)
            btn.get_style_context().add_class("tray-profile-btn")
            btn.add(LineIcon(kind, 22))
            btn.connect("clicked", self._on_profile, mode)
            prof_row.pack_start(btn, True, True, 0)
            self._profile_btns[mode] = btn
        prof_box.pack_start(prof_row, True, True, 0)
        mid.pack_start(prof_box, True, True, 0)
        shell.pack_start(mid, False, False, 0)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        turbo_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        turbo_card.get_style_context().add_class("tray-bottom-card")
        turbo_card.set_size_request(168, -1)
        turbo_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        turbo_head.pack_start(LineIcon("bolt", 16), False, False, 0)
        tl = Gtk.Label(label="Turbo", halign=Gtk.Align.START)
        tl.get_style_context().add_class("tray-field-label")
        turbo_head.pack_start(tl, False, False, 0)
        turbo_card.pack_start(turbo_head, False, False, 0)
        self.turbo_combo = Gtk.ComboBoxText()
        for label, _mode in (("Auto", "auto"), ("Off", "never"), ("On", "always")):
            self.turbo_combo.append_text(label)
        self.turbo_combo.get_style_context().add_class("tray-combo")
        self.turbo_combo.connect("changed", self._on_turbo_changed)
        turbo_card.pack_start(self.turbo_combo, False, False, 0)
        bottom.pack_start(turbo_card, False, False, 0)

        charge_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        charge_card.get_style_context().add_class("tray-bottom-card")
        charge_card.set_hexpand(True)
        charge_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        charge_row.pack_start(LineIcon("battery", 16), False, False, 0)
        cl = Gtk.Label(label="Charge Limit", halign=Gtk.Align.START)
        cl.get_style_context().add_class("tray-field-label")
        charge_row.pack_start(cl, False, False, 0)
        self.charge_on = Gtk.Switch()
        self.charge_on.set_valign(Gtk.Align.CENTER)
        self.charge_on.get_style_context().add_class("tray-switch")
        self.charge_on.connect("notify::active", self._on_charge_toggle)
        charge_row.pack_start(self.charge_on, False, False, 0)
        charge_row.pack_start(Gtk.Box(), True, True, 0)
        minus = Gtk.Button(label="−")
        minus.get_style_context().add_class("tray-step-btn")
        minus.connect("clicked", self._charge_step, -1)
        plus = Gtk.Button(label="+")
        plus.get_style_context().add_class("tray-step-btn")
        plus.connect("clicked", self._charge_step, 1)
        self.charge_pct = Gtk.Label(label="80%")
        self.charge_pct.get_style_context().add_class("tray-stat")
        charge_row.pack_start(self.charge_pct, False, False, 0)
        charge_row.pack_start(minus, False, False, 0)
        charge_row.pack_start(plus, False, False, 0)
        charge_card.pack_start(charge_row, False, False, 0)
        self.charge_bar = Gtk.ProgressBar()
        self.charge_bar.get_style_context().add_class("tray-charge-bar")
        self.charge_bar.set_show_text(False)
        charge_card.pack_start(self.charge_bar, False, False, 0)
        bottom.pack_start(charge_card, True, True, 0)
        shell.pack_start(bottom, False, False, 0)

        self._charge_value = 80
        self._tick_id = GLib.timeout_add_seconds(2, self._refresh_tick)
        self.refresh()

    def _paint_logo(self, _widget, cr) -> bool:
        w = _widget.get_allocated_width()
        h = _widget.get_allocated_height()
        radius = 8
        cr.set_source_rgb(0.18, 0.42, 0.95)
        cr.new_sub_path()
        cr.arc(w - radius, radius, radius, -math.pi / 2, 0)
        cr.arc(w - radius, h - radius, radius, 0, math.pi / 2)
        cr.arc(radius, h - radius, radius, math.pi / 2, math.pi)
        cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.fill()
        cr.set_source_rgb(1, 1, 1)
        _draw_lightning(cr, w / 2, h / 2, min(w, h) * 0.62)
        cr.fill()
        return False

    def _round_rect(self, cr, x, y, w, h, r) -> None:
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _paint_chrome(self, _widget, cr) -> bool:
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        if w < 20 or h < 20:
            return False
        tail = 12
        if self._tail_down:
            top, body_h = 1, h - tail - 2
        else:
            top, body_h = tail + 1, h - tail - 2
        cr.set_source_rgba(0.07, 0.09, 0.14, 0.98)
        self._round_rect(cr, 1, top, w - 2, body_h, 20)
        cr.fill()
        cr.set_source_rgba(0.28, 0.55, 1.0, 0.55)
        cr.set_line_width(1.4)
        self._round_rect(cr, 1.5, top, w - 3, body_h, 20)
        cr.stroke()
        tx = max(28, min(w - 28, self._tail_x))
        cr.set_source_rgba(0.07, 0.09, 0.14, 0.98)
        if self._tail_down:
            cr.move_to(tx - 11, top + body_h - 1)
            cr.line_to(tx, h - 1)
            cr.line_to(tx + 11, top + body_h - 1)
        else:
            cr.move_to(tx - 11, top + 1)
            cr.line_to(tx, 1)
            cr.line_to(tx + 11, top + 1)
        cr.close_path()
        cr.fill()
        return False

    def _on_key(self, _w, event) -> bool:
        if event.keyval == Gdk.KEY_Escape:
            self.hide()
            return True
        return False

    def _open_monitor(self) -> None:
        self.hide()
        self.app._present_window()
        if self.app.window:
            self.app.window._show_page("monitor")

    def _open_settings(self) -> None:
        self.hide()
        self.app._present_window()
        if self.app.window:
            self.app.window._show_page("settings")

    def _current_tray_profile(self) -> str | None:
        mode = read_samsung_mode()
        if mode == "adaptive":
            return None
        if mode == "power" and read_turbo_override() == "always":
            return "turbo"
        return mode if mode in self._profile_btns else None

    def _sync_profile_buttons(self) -> None:
        current = self._current_tray_profile()
        for mode, btn in self._profile_btns.items():
            ctx = btn.get_style_context()
            if mode == current:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")
            btn.queue_draw()

    def refresh(self) -> None:
        live = read_live()
        self.gauge.set_value(float(live["cpu_pct"]))
        freq = f"{live['freq_ghz']:.2f} GHz" if live["freq_ghz"] else "—"
        temp = f"{live['temp']:.0f} °C" if live["temp"] is not None else "—"
        watts = f"{live['rate_w']:.1f} W" if live["rate_w"] is not None else "—"
        self.stat_freq.set_text(freq)
        self.stat_temp.set_text(temp)
        self.stat_watts.set_text(watts)
        if is_auto_cpufreq_daemon_active():
            self.status_lbl.set_markup("<span color='#3ddc84'>●</span>  <span color='#c5d0e4'>Running</span>")
        else:
            self.status_lbl.set_markup("<span color='#f87171'>●</span>  <span color='#c5d0e4'>Daemon off</span>")
        self._updating = True
        turbo = read_turbo_override()
        turbo_idx = {"auto": 0, "never": 1, "always": 2}.get(turbo, 0)
        self.turbo_combo.set_active(turbo_idx)
        limit = read_charge_limit()
        if limit is None:
            self.charge_on.set_sensitive(False)
            self._charge_value = 80
        else:
            self.charge_on.set_sensitive(True)
            self._charge_value = limit
            self.charge_on.set_active(limit < 100)
        self._paint_charge()
        self._sync_profile_buttons()
        self._updating = False

    def _refresh_tick(self) -> bool:
        if self.get_visible():
            self.refresh()
        return True

    def _paint_charge(self) -> None:
        pct = self._charge_value
        self.charge_pct.set_text(f"{pct}%")
        self.charge_bar.set_fraction(max(0.0, min(1.0, pct / 100.0)))

    def _charge_step(self, _btn, delta: int) -> None:
        if not self.charge_on.get_active():
            return
        self._charge_value = max(50, min(100, self._charge_value + delta))
        self._paint_charge()
        self._apply_charge()

    def _on_charge_toggle(self, switch: Gtk.Switch, _pspec) -> None:
        if self._updating:
            return
        if switch.get_active():
            self._apply_charge()
        else:
            self._charge_value = 100
            self._paint_charge()

            def work():
                return set_charge_limit(100)

            def done(ok: bool, _msg: str) -> None:
                if ok:
                    self.refresh()
                    self.app._on_changed()

            schedule_apply(work, done)

    def _apply_charge(self) -> None:
        if not self.charge_on.get_active() and read_charge_limit() == 100:
            return
        pct = self._charge_value

        def work():
            return set_charge_limit(pct)

        def done(ok: bool, _msg: str) -> None:
            if ok:
                self.refresh()
                self.app._on_changed()

        schedule_apply(work, done)

    def _on_turbo_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._updating:
            return
        idx = combo.get_active()
        mode = ("auto", "never", "always")[idx if 0 <= idx <= 2 else 0]
        previous = read_turbo_override()

        def work():
            return run_turbo(mode)

        def done(ok: bool, _msg: str) -> None:
            if ok:
                self.refresh()
                self.app._on_changed()
            else:
                self._updating = True
                prev_idx = {"auto": 0, "never": 1, "always": 2}.get(previous, 0)
                combo.set_active(prev_idx)
                self._updating = False

        schedule_apply(work, done)

    def _on_profile(self, _btn: Gtk.Button, mode: str) -> None:
        if mode == self._current_tray_profile():
            return
        previous_mode = read_samsung_mode()

        def work():
            if mode == "turbo":
                ok, msg = run_profile("power")
                if not ok:
                    return ok, msg
                return run_turbo("always")
            ok, msg = run_profile(mode)
            if ok and read_turbo_override() == "always" and mode != "power":
                ok2, msg2 = run_turbo("auto")
                if not ok2:
                    return ok2, msg2
            return ok, msg

        def done(ok: bool, _msg: str) -> None:
            if ok:
                self.refresh()
                self.app._on_changed()
            else:
                self._sync_profile_buttons()

        schedule_apply(work, done)

    def present_at(self, anchor_x: int, anchor_y: int, panel_position: int = 3) -> None:
        self.refresh()
        self.show_all()
        w, h = 680, 332
        self.resize(w, h)
        # GTK position type: 2 top, 3 bottom, 0 left, 1 right.
        self._tail_down = panel_position != 2
        if self._tail_down:
            origin_x = int(anchor_x - w / 2)
            origin_y = int(anchor_y - h - 4)
        else:
            origin_x = int(anchor_x - w / 2)
            origin_y = int(anchor_y + 6)
        display = Gdk.Display.get_default()
        monitor = None
        if display is not None and hasattr(display, "get_monitor_at_point"):
            monitor = display.get_monitor_at_point(int(anchor_x), int(anchor_y))
        if monitor is None and display is not None:
            monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor is not None:
            geo = monitor.get_geometry()
            origin_x = max(geo.x + 8, min(origin_x, geo.x + geo.width - w - 8))
            origin_y = max(geo.y + 8, min(origin_y, geo.y + geo.height - h - 8))
        self._tail_x = int(anchor_x - origin_x)
        self.move(origin_x, origin_y)
        self.present()
        self.grab_focus()
        self.queue_draw()

    def present_near_tray(self) -> None:
        display = Gdk.Display.get_default()
        x = y = 0
        if display is not None:
            seat = display.get_default_seat()
            pointer = seat.get_pointer() if seat else None
            if pointer is not None:
                _screen, x, y = pointer.get_position()
        if not x and not y:
            monitor = display.get_primary_monitor() if display else None
            if monitor is not None:
                geo = monitor.get_geometry()
                x = geo.x + geo.width // 2
                y = geo.y + geo.height - 8
        self.present_at(int(x), int(y), 3)


def schedule_apply(work, on_done) -> bool:
    """Run a privileged action after this click/menu event finishes.

    The tray menu holds an input grab until the activate handler returns.
    Starting pkexec inside that handler hides the password dialog and makes
    the item look like it did not click.
    """
    if not _APPLY_LOCK.acquire(blocking=False):
        return False

    def start() -> bool:
        def runner() -> None:
            try:
                result = work()
            except Exception as exc:
                result = (False, str(exc))

            def finish() -> bool:
                try:
                    on_done(*result)
                finally:
                    _APPLY_LOCK.release()
                return False

            GLib.idle_add(finish)

        threading.Thread(target=runner, daemon=True).start()
        return False

    GLib.idle_add(start)
    return True


def _admin_schema():
    gi.require_version("Secret", "1")
    from gi.repository import Secret

    return Secret.Schema.new(
        "com.zorin.cpu-power.AdminPassword",
        Secret.SchemaFlags.NONE,
        {"app": Secret.SchemaAttributeType.STRING},
    )


def load_admin_password() -> str | None:
    try:
        gi.require_version("Secret", "1")
        from gi.repository import Secret

        stored = Secret.password_lookup_sync(_admin_schema(), {"app": "zorin-cpu-power"}, None)
    except (GLib.Error, ValueError, ImportError):
        return None
    return stored or None


def admin_password_saved() -> bool:
    return bool(load_admin_password())


def _sudo_rejected(text: str) -> bool:
    lower = text.lower()
    return any(
        phrase in lower
        for phrase in (
            "incorrect password",
            "sorry, try again",
            "authentication failure",
            "no password was provided",
        )
    )


def _clean_sudo_output(text: str) -> str:
    kept = []
    for line in text.splitlines():
        lower = line.lower().strip()
        if not lower or lower.startswith("[sudo]") or lower == "password:":
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _run_sudo(password: str, args: list[str], timeout: int) -> tuple[bool, str]:
    cmd = ["sudo", "-S", "-k", "-p", "", "--", str(PRIV_HELPER), *args]
    try:
        proc = subprocess.run(
            cmd,
            input=password + "\n",
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "Timed out while applying the change."
    except FileNotFoundError:
        return False, "sudo was not found."
    blob = (proc.stderr or "") + "\n" + (proc.stdout or "")
    if proc.returncode != 0 and _sudo_rejected(blob):
        return False, "Saved password was rejected. Enter the new one in Settings."
    out = _clean_sudo_output((proc.stdout or "") + "\n" + (proc.stderr or ""))
    if proc.returncode != 0:
        return False, out or f"Command failed (exit {proc.returncode})."
    return True, out


def check_admin_password(password: str) -> tuple[bool, str]:
    if not password:
        return False, "Enter your login password."
    try:
        proc = subprocess.run(
            ["sudo", "-S", "-k", "-p", "", "--", "true"],
            input=password + "\n",
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return False, "Password check timed out."
    except FileNotFoundError:
        return False, "sudo was not found."
    if proc.returncode != 0:
        return False, "That password was not accepted."
    return True, ""


def save_admin_password(password: str) -> tuple[bool, str]:
    ok, msg = check_admin_password(password)
    if not ok:
        return ok, msg
    try:
        gi.require_version("Secret", "1")
        from gi.repository import Secret

        stored = Secret.password_store_sync(
            _admin_schema(),
            {"app": "zorin-cpu-power"},
            Secret.COLLECTION_DEFAULT,
            "CPU Power login password",
            password,
            None,
        )
    except (GLib.Error, ValueError, ImportError) as exc:
        return False, f"Could not save the password ({exc})."
    if not stored:
        return False, "Could not save the password in the login keyring."
    return True, "Password saved. Changes apply without asking."


def forget_admin_password() -> tuple[bool, str]:
    try:
        gi.require_version("Secret", "1")
        from gi.repository import Secret

        Secret.password_clear_sync(_admin_schema(), {"app": "zorin-cpu-power"}, None)
    except (GLib.Error, ValueError, ImportError) as exc:
        return False, f"Could not remove the saved password ({exc})."
    return True, "Saved password removed. Changes will ask again."


def run_pkexec(args: list[str], timeout: int = 120) -> tuple[bool, str]:
    # A saved login password applies the helper with sudo and skips the dialog.
    if PRIV_HELPER.is_file():
        password = load_admin_password()
        if password:
            return _run_sudo(password, args, timeout)
        cmd = ["pkexec", str(PRIV_HELPER)] + args
    else:
        cmd = ["pkexec"] + args
    env = os.environ.copy()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "Timed out waiting for the password dialog."
    except FileNotFoundError:
        return False, "pkexec or target command not found."

    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        if proc.returncode == 126:
            return False, "Password dialog was closed before the change was applied."
        if proc.returncode == 127:
            return False, out or "Could not start the password prompt."
        return False, out or f"Command failed (exit {proc.returncode})."
    return True, out


def start_auto_cpufreq_daemon() -> None:
    # A plain systemctl start asks for a password through PolicyKit on every
    # profile or turbo change, even after the helper already applied it.
    if is_auto_cpufreq_daemon_active():
        return
    password = load_admin_password()
    if not password:
        return
    try:
        subprocess.run(
            ["sudo", "-S", "-p", "", "--", "systemctl", "start", "auto-cpufreq.service"],
            input=password + "\n",
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def is_auto_cpufreq_daemon_active() -> bool:
    try:
        proc = subprocess.run(
            ["systemctl", "is-active", "auto-cpufreq.service"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "active"


def needs_daemon_install(message: str) -> bool:
    lower = message.lower()
    return (
        "not running in daemon mode" in lower
        or "auto-cpufreq is not running" in lower
        or "auto-cpufreq --install" in lower
    )


def run_install_daemon() -> tuple[bool, str]:
    if PRIV_HELPER.is_file():
        ok, msg = run_pkexec(["install-daemon"], timeout=300)
        if ok or ("Usage:" not in msg and "Rejected argument" not in msg):
            if ok:
                start_auto_cpufreq_daemon()
                if is_auto_cpufreq_daemon_active():
                    return True, "auto-cpufreq daemon installed and running."
                return True, "Install finished. If profiles still fail, reboot once."
            return ok, msg

    cmd = ["pkexec", str(AUTO_CPUFREQ), "--install"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, env=os.environ.copy())
    except subprocess.TimeoutExpired:
        return False, "Timed out while installing the daemon."
    except FileNotFoundError:
        return False, "pkexec or auto-cpufreq not found."
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        return False, out or f"Install failed (exit {proc.returncode})."
    start_auto_cpufreq_daemon()
    if is_auto_cpufreq_daemon_active():
        return True, "auto-cpufreq daemon installed and running."
    return True, out or "auto-cpufreq install completed."


def read_override() -> str:
    if not OVERRIDE_FILE.is_file():
        return "default"
    try:
        with OVERRIDE_FILE.open("rb") as fh:
            value = pickle.load(fh)
        return value if value in ("powersave", "performance") else "default"
    except (OSError, pickle.UnpicklingError):
        return "default"


def read_turbo_override() -> str:
    if not TURBO_OVERRIDE_FILE.is_file():
        return "auto"
    try:
        with TURBO_OVERRIDE_FILE.open("rb") as fh:
            value = pickle.load(fh)
        return value if value in ("never", "always") else "auto"
    except (OSError, pickle.UnpicklingError):
        return "auto"


def read_charge_limit() -> int | None:
    if not BATTERY_SYSFS.is_file():
        return None
    try:
        return int(BATTERY_SYSFS.read_text().strip())
    except (OSError, ValueError):
        return None


def read_auto_cpufreq_conf() -> dict[str, dict[str, str]]:
    data: dict[str, dict[str, str]] = {
        "charger": {"governor": "performance", "turbo": "auto"},
        "battery": {"governor": "powersave", "turbo": "auto"},
    }
    if not AUTO_CPUFREQ_CONF.is_file():
        return data
    section = None
    for line in AUTO_CPUFREQ_CONF.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].lower()
            continue
        if section in data and "=" in line:
            key, val = (p.strip() for p in line.split("=", 1))
            if key in ("governor", "turbo"):
                data[section][key] = val
    return data


def read_power_status() -> str:
    parts: list[str] = []
    try:
        out = subprocess.run(
            ["upower", "-i", "/org/freedesktop/UPower/devices/battery_BAT1"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in out.stdout.splitlines():
            if "percentage:" in line:
                parts.append(line.split(":", 1)[1].strip())
            if "state:" in line:
                parts.append(line.split(":", 1)[1].strip())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    gov = Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor")
    if gov.is_file():
        parts.append(f"gov {gov.read_text().strip()}")
    limit = read_charge_limit()
    if limit is not None:
        parts.append(f"limit {limit}%")
    return " · ".join(parts) if parts else ""


def read_samsung_mode() -> str:
    try:
        value = SAMSUNG_MODE_FILE.read_text().strip()
    except OSError:
        value = ""
    if value in SAMSUNG_LABELS:
        return value
    override = read_override()
    return {"powersave": "quiet", "performance": "power"}.get(override, "adaptive")


def write_samsung_mode(mode: str) -> None:
    SAMSUNG_MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SAMSUNG_MODE_FILE.write_text(mode)


def default_battery_icon() -> dict:
    settings = {
        key: {"enabled": True, "color": color}
        for key, _label, color in BATTERY_ICON_MODES
    }
    turbo_key, _label, turbo_color = TURBO_PERCENT
    settings[turbo_key] = {"enabled": True, "color": turbo_color}
    return settings


def _battery_color_item(raw: dict, key: str, fallback: str) -> tuple[str, bool]:
    item = raw.get(key)
    if not isinstance(item, dict):
        return fallback, True
    chosen = str(item.get("color") or fallback).strip() or fallback
    return chosen, bool(item.get("enabled", True))


def load_battery_icon() -> dict:
    settings = default_battery_icon()
    try:
        raw = json.loads(BATTERY_ICON_FILE.read_text())
    except (OSError, json.JSONDecodeError, TypeError):
        return settings
    if not isinstance(raw, dict):
        return settings
    for key, _label, color in BATTERY_ICON_MODES:
        chosen, enabled = _battery_color_item(raw, key, color)
        settings[key]["color"] = chosen
        settings[key]["enabled"] = enabled
    turbo_key, _label, turbo_color = TURBO_PERCENT
    if isinstance(raw.get(turbo_key), dict):
        chosen, enabled = _battery_color_item(raw, turbo_key, turbo_color)
    elif isinstance(raw.get("adaptive"), dict):
        chosen, enabled = _battery_color_item(raw, "adaptive", turbo_color)
        enabled = True
    else:
        chosen, enabled = turbo_color, True
    settings[turbo_key]["color"] = chosen
    settings[turbo_key]["enabled"] = enabled
    return settings


def save_battery_icon(settings: dict) -> None:
    payload = {}
    defaults = default_battery_icon()
    for key, _label, _color in (*BATTERY_ICON_MODES, TURBO_PERCENT):
        item = settings.get(key) if isinstance(settings.get(key), dict) else {}
        payload[key] = {
            "enabled": bool(item.get("enabled", defaults[key]["enabled"])),
            "color": str(item.get("color") or defaults[key]["color"]),
        }
    BATTERY_ICON_FILE.parent.mkdir(parents=True, exist_ok=True)
    BATTERY_ICON_FILE.write_text(json.dumps(payload, indent=2) + "\n")


def rgba_to_hex(rgba: Gdk.RGBA) -> str:
    return f"#{int(round(rgba.red * 255)):02X}{int(round(rgba.green * 255)):02X}{int(round(rgba.blue * 255)):02X}"


def hex_to_rgb(value: str) -> tuple[float, float, float] | None:
    text = value.strip().lstrip("#")
    if len(text) != 6:
        return None
    try:
        return (
            int(text[0:2], 16) / 255,
            int(text[2:4], 16) / 255,
            int(text[4:6], 16) / 255,
        )
    except ValueError:
        return None


def running_battery_mode() -> str:
    """Mode whose color the battery should show. Adaptive follows the live firmware profile."""
    mode = read_samsung_mode()
    if mode != "adaptive":
        return mode
    platform_to_mode = {platform: name for name, platform in SAMSUNG_PLATFORM.items()}
    return platform_to_mode.get(read_platform_profile(), "balance")


def battery_profile_color() -> tuple[float, float, float] | None:
    mode = running_battery_mode()
    entry = load_battery_icon().get(mode) or {}
    if not entry.get("enabled", True):
        return None
    return hex_to_rgb(str(entry.get("color") or ""))


def read_platform_profile() -> str:
    return _read_file(str(PLATFORM_PROFILE)) or "balanced"


def choose_samsung_platform(load: float, temp: float | None, plugged: bool, previous: str | None) -> str:
    """Pick Silent, Quiet, Balance, or Power. Idle fan is already off, so Quiet and Balance matter once the CPU is warm."""
    temp = 40.0 if temp is None else temp
    if temp >= 78:
        raw = "performance" if plugged and load >= 40 else "balanced"
    elif plugged:
        if load >= 50:
            raw = "performance"
        elif load >= 20:
            raw = "balanced"
        elif load <= 8 and temp < 48:
            raw = "low-power"
        else:
            raw = "quiet"
    elif load >= 60:
        raw = "balanced"
    elif load >= 18:
        raw = "quiet"
    elif load <= 8 and temp < 46:
        raw = "low-power"
    else:
        raw = "quiet"
    if previous == "performance" and plugged and load >= 35 and temp < 82:
        raw = "performance"
    elif previous == "low-power" and load <= 14 and temp < 52:
        raw = "low-power"
    elif previous == "quiet" and 8 < load < 28 and temp < 72:
        raw = "quiet"
    elif previous == "balanced" and 15 < load < 58 and temp < 80:
        raw = "balanced"
    return hold_until_cool(raw, previous, temp)


def hold_until_cool(choice: str, previous: str | None, temp: float) -> str:
    """After Power or Balance, keep that fan until the CPU has cooled.

    Load falls as soon as a hard task ends. Temperature does not. Quiet and
    Silent would drop the fan while the laptop is still hot.
    """
    if choice == "low-power" and temp >= 48:
        choice = "quiet"
    if choice in ("quiet", "low-power") and temp >= 55 and previous in ("performance", "balanced"):
        return "performance" if previous == "performance" else "balanced"
    return choice


def cooling_hold(choice: str, load: float, temp: float | None, previous: str | None) -> bool:
    """Power fan, but let the CPU clocks fall so the temperature can come down."""
    if temp is None:
        return False
    return choice == "performance" and previous == "performance" and load < 35 and temp >= 55


POWER_RULES = ("adaptive", "powersave", "silent", "quiet", "balance", "power")
POWER_RULE_LABELS = {
    "adaptive": "Adaptive (all 4)",
    "powersave": "Power save (Silent + Quiet)",
    "silent": "Force silent",
    "quiet": "Force quiet",
    "balance": "Force balance",
    "power": "Force power",
}
RULES_PATH = Path.home() / ".config" / "zorin-cpu-power" / "power-rules.json"
PLATFORM_KIND = {
    "low-power": "silent",
    "quiet": "quiet",
    "balanced": "balance",
    "performance": "power",
}


def normalize_power_rule(value: str | None, fallback: str = "adaptive") -> str:
    aliases = {"default": "adaptive", "balanced": "balance", "performance": "power", "powersave": "powersave"}
    value = aliases.get(value or "", value or "")
    return value if value in POWER_RULES else fallback


def load_power_rules() -> dict[str, str]:
    data = {"charger": "adaptive", "battery": "powersave"}
    try:
        saved = json.loads(RULES_PATH.read_text())
        data["charger"] = normalize_power_rule(saved.get("charger"), "adaptive")
        data["battery"] = normalize_power_rule(saved.get("battery"), "powersave")
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return data


def save_power_rules(data: dict[str, str]) -> None:
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    RULES_PATH.write_text(json.dumps({
        "charger": normalize_power_rule(data.get("charger"), "adaptive"),
        "battery": normalize_power_rule(data.get("battery"), "powersave"),
    }, indent=2))


def choose_powersave_platform(load: float, temp: float | None, previous: str | None) -> str:
    """Power save stays in Silent or Quiet. Quiet when there is work or warmth, Silent when idle and cool."""
    temp = 40.0 if temp is None else temp
    raw = "quiet" if load >= 16 or temp >= 52 else "low-power"
    if previous == "low-power" and load <= 22 and temp < 58:
        return "low-power"
    if previous == "quiet" and (load >= 10 or temp >= 48):
        return "quiet"
    return raw


def platform_for_rule(rule: str, load: float, temp: float | None, plugged: bool, previous: str | None) -> str:
    rule = normalize_power_rule(rule)
    if rule == "adaptive":
        return choose_samsung_platform(load, temp, plugged, previous)
    if rule == "powersave":
        return choose_powersave_platform(load, temp, previous)
    return SAMSUNG_PLATFORM[rule]


def apply_platform_choice(platform: str, cooling: bool = False) -> tuple[bool, str]:
    kind = "cool" if cooling else PLATFORM_KIND.get(platform)
    if not kind:
        return False, f"Unknown platform profile: {platform}"
    ok, msg = run_pkexec(["samsung-mode", "apply", platform, kind])
    if not ok and "Rejected" in msg and not cooling:
        ok, msg = run_pkexec(["samsung-mode", "platform", platform])
    if not ok:
        return ok, msg
    if cooling:
        return True, "Cooling down on the Power fan"
    shown = {"low-power": "Silent", "quiet": "Quiet", "balanced": "Balance", "performance": "Power"}[platform]
    return True, f"Using {shown}"


def run_profile(mode: str) -> tuple[bool, str]:
    aliases = {"default": "adaptive", "powersave": "quiet", "performance": "power"}
    mode = aliases.get(mode, mode)
    if mode not in SAMSUNG_LABELS:
        return False, f"Unknown profile: {mode}"
    ok, msg = run_pkexec(["samsung-mode", mode])
    if not ok and "Rejected mode" in msg:
        platform = SAMSUNG_PLATFORM.get(mode)
        if platform:
            ok, msg = run_pkexec(["samsung-mode", "platform", platform])
    if ok:
        write_samsung_mode(mode)
        start_auto_cpufreq_daemon()
        return True, f"{SAMSUNG_LABELS[mode]} applied"
    return ok, msg


def set_platform_profile(platform: str) -> tuple[bool, str]:
    if platform not in ("low-power", "quiet", "balanced", "performance"):
        return False, f"Unknown platform profile: {platform}"
    ok, msg = run_pkexec(["samsung-mode", "platform", platform])
    if ok:
        label = {v: SAMSUNG_LABELS[k] for k, v in SAMSUNG_PLATFORM.items()}.get(platform, platform)
        return True, f"Adaptive chose {label}"
    return ok, msg


def run_turbo(mode: str) -> tuple[bool, str]:
    if mode not in TURBO_MODES:
        return False, f"Unknown turbo mode: {mode}"
    ok, msg = run_pkexec(["auto-cpufreq", f"--turbo={mode}"])
    if ok:
        start_auto_cpufreq_daemon()
        labels = {"auto": "Turbo: automatic", "never": "Turbo: off", "always": "Turbo: always on"}
        return True, labels[mode]
    return ok, msg


def set_charge_limit(percent: int) -> tuple[bool, str]:
    if percent < 50 or percent > 100:
        return False, "Charge limit must be between 50 and 100."
    if not BATTERY_SYSFS.is_file():
        return False, "This laptop does not expose charge_control_end_threshold."
    ok, msg = run_pkexec(["set-charge", str(percent)])
    if ok:
        USER_CONF.parent.mkdir(parents=True, exist_ok=True)
        USER_CONF.write_text(str(percent))
        return True, f"Charge limit set to {percent}%"
    return ok, msg


def save_daemon_config(charger: dict[str, str], battery: dict[str, str]) -> tuple[bool, str]:
    body = (
        "[charger]\n"
        f"governor = {charger['governor']}\n"
        f"turbo = {charger['turbo']}\n\n"
        "[battery]\n"
        f"governor = {battery['governor']}\n"
        f"turbo = {battery['turbo']}\n"
    )
    with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
        tmp.write(body)
        tmp_path = tmp.name
    ok, msg = run_pkexec(["write-config", tmp_path])
    Path(tmp_path).unlink(missing_ok=True)
    if not ok:
        return ok, msg
    start_auto_cpufreq_daemon()
    return True, "Saved auto-cpufreq rules for charger and battery."


def read_scaling_governor() -> str:
    return _read_file("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor") or "unknown"


def read_turbo_live() -> bool | None:
    no_turbo = _read_file("/sys/devices/system/cpu/intel_pstate/no_turbo")
    if no_turbo in ("0", "1"):
        return no_turbo == "0"
    boost = _read_file("/sys/devices/system/cpu/cpufreq/boost")
    if boost in ("0", "1"):
        return boost == "1"
    return None


def live_governor_mode_label(governor: str) -> str:
    if governor == "powersave":
        return "Power saver"
    if governor == "performance":
        return "Performance"
    return "Normal"


def format_profile_status(override: str | None = None, governor: str | None = None) -> str:
    mode = read_samsung_mode()
    label = SAMSUNG_LABELS.get(mode, mode)
    if mode == "adaptive":
        current = {
            "low-power": "Silent",
            "quiet": "Quiet",
            "balanced": "Balance",
            "performance": "Power",
        }.get(read_platform_profile(), "")
        return f"Adaptive · {current}" if current else "Adaptive"
    return label


def format_turbo_status(override: str | None = None, turbo_live: bool | None = None) -> str:
    override = read_turbo_override() if override is None else override
    if override == "never":
        return "off"
    if override == "always":
        return "always"
    turbo_live = read_turbo_live() if turbo_live is None else turbo_live
    if turbo_live is True:
        return "auto.on"
    if turbo_live is False:
        return "auto.off"
    return "auto"


def read_status_footer() -> dict[str, str]:
    prof = read_override()
    prof_label = format_profile_status(prof)
    turbo = read_turbo_override()
    turbo_label = format_turbo_status(turbo)
    conf = read_auto_cpufreq_conf()
    cg = GOVERNOR_LABELS.get(conf["charger"]["governor"], conf["charger"]["governor"])
    bg = GOVERNOR_LABELS.get(conf["battery"]["governor"], conf["battery"]["governor"])
    limit = read_charge_limit()
    charge = f"{limit}%" if limit is not None else "—"
    return {
        "profile": prof_label,
        "turbo": turbo_label,
        "charge": charge,
        "gov_charger": cg,
        "gov_battery": bg,
    }


def _combo_row(
    label: str,
    values: tuple[str, ...],
    active: str,
    labels: dict[str, str] | None = None,
) -> tuple[Gtk.Label, Gtk.ComboBoxText]:
    lbl = Gtk.Label(label=label)
    lbl.set_halign(Gtk.Align.START)
    combo = Gtk.ComboBoxText()
    for v in values:
        combo.append_text(labels.get(v, v) if labels else v)
    combo.set_active(max(0, values.index(active) if active in values else 0))
    return lbl, combo


def _combo_value(combo: Gtk.ComboBoxText, values: tuple[str, ...]) -> str:
    idx = combo.get_active()
    if idx < 0 or idx >= len(values):
        return values[0]
    return values[idx]


    return values[idx]


def _card_header(icon: str, title: str, subtitle: str) -> Gtk.Box:
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    icon_l = Gtk.Label(label=icon)
    icon_l.get_style_context().add_class("profile-tile-icon")
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    t = Gtk.Label(label=title)
    t.get_style_context().add_class("cpu-card-title")
    t.set_halign(Gtk.Align.START)
    s = Gtk.Label(label=subtitle)
    s.get_style_context().add_class("cpu-card-desc")
    s.set_halign(Gtk.Align.START)
    s.set_line_wrap(True)
    text.pack_start(t, False, False, 0)
    text.pack_start(s, False, False, 0)
    row.pack_start(icon_l, False, False, 0)
    row.pack_start(text, True, True, 0)
    return row


def _wrap_card(widget: Gtk.Widget) -> Gtk.Frame:
    frame = Gtk.Frame()
    frame.get_style_context().add_class("cpu-card")
    frame.add(widget)
    return frame


class ProfileTiles:
    PROFILES = (
        ("✦", "Adaptive", "adaptive", "Chooses Silent, Quiet, Balance, or Power."),
        ("🔇", "Silent", "silent", "Lowest fan and power while the CPU is idle."),
        ("🤫", "Quiet", "quiet", "Holds the fan down for light work."),
        ("⚖", "Balance", "balance", "Normal fan and performance."),
        ("⚡", "Power", "power", "Highest speed and the strongest fan curve."),
    )

    def __init__(self, current: str, on_pick) -> None:
        self.on_pick = on_pick
        self.current = current
        self.tiles: dict[str, Gtk.Button] = {}
        self.box = Gtk.FlowBox()
        self.box.set_max_children_per_line(5)
        self.box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.box.set_homogeneous(True)
        self.box.set_column_spacing(8)
        self.box.set_row_spacing(8)
        for icon, title, mode, desc in self.PROFILES:
            btn = Gtk.Button()
            btn.get_style_context().add_class("profile-tile")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            inner.set_halign(Gtk.Align.CENTER)
            ic = Gtk.Label(label=icon)
            ic.get_style_context().add_class("profile-tile-icon")
            tl = Gtk.Label(label=title)
            tl.get_style_context().add_class("profile-tile-title")
            dl = Gtk.Label(label=desc)
            dl.get_style_context().add_class("profile-tile-desc")
            dl.set_line_wrap(True)
            dl.set_max_width_chars(14)
            dl.set_justify(Gtk.Justification.CENTER)
            inner.pack_start(ic, False, False, 0)
            inner.pack_start(tl, False, False, 0)
            inner.pack_start(dl, False, False, 0)
            btn.add(inner)
            btn.connect("clicked", self._on_click, mode)
            self.tiles[mode] = btn
            self.box.add(btn)
        self.set_current(current if current in self.tiles else "adaptive")

    def _on_click(self, _btn: Gtk.Button, mode: str) -> None:
        if mode == self.current:
            return
        previous = self.current
        self.set_current(mode)
        self.on_pick(mode, previous)

    def set_current(self, mode: str) -> None:
        if mode not in self.tiles:
            return
        self.current = mode
        for m, btn in self.tiles.items():
            ctx = btn.get_style_context()
            if m == mode:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")


class SegmentedChoice:
    def __init__(
        self,
        options: tuple[tuple[str, str], ...],
        current: str,
        on_pick,
        css_class: str = "turbo-segment",
    ) -> None:
        self.on_pick = on_pick
        self.options = options
        self.current = current
        self.buttons: dict[str, Gtk.Button] = {}
        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.box.get_style_context().add_class(css_class)
        self.box.set_halign(Gtk.Align.START)
        for label, mode in options:
            btn = Gtk.Button(label=label)
            btn.connect("clicked", self._on_click, mode)
            self.buttons[mode] = btn
            self.box.pack_start(btn, False, False, 0)
        known = {m for _, m in options}
        self.set_current(current if current in known else options[0][1])

    def _on_click(self, _btn: Gtk.Button, mode: str) -> None:
        if mode == self.current:
            return
        previous = self.current
        self.set_current(mode)
        self.on_pick(mode, previous)

    def set_current(self, mode: str) -> None:
        if mode not in self.buttons:
            return
        self.current = mode
        for m, btn in self.buttons.items():
            ctx = btn.get_style_context()
            if m == mode:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")


class ChoiceBar(SegmentedChoice):
    """Horizontal linked bar (float bar)."""

    def __init__(self, options: tuple[tuple[str, str], ...], current: str, on_pick) -> None:
        super().__init__(options, current, on_pick, css_class="turbo-segment")


class TurboControl:
    """Auto mode button plus manual on/off switch (switch disabled while Auto is active)."""

    def __init__(self, current: str, on_pick) -> None:
        self.on_pick = on_pick
        self.current = current
        self._syncing = False
        self._live_turbo: bool | None = None

        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.auto_btn = Gtk.Button(label="Auto")
        self.auto_btn.set_halign(Gtk.Align.START)
        self.auto_btn.get_style_context().add_class("turbo-auto-btn")
        self.auto_btn.connect("clicked", self._on_auto_clicked)
        self.box.pack_start(self.auto_btn, False, False, 0)

        self.manual_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.manual_row.get_style_context().add_class("turbo-manual-row")
        self.manual_row.set_halign(Gtk.Align.START)
        turbo_lbl = Gtk.Label(label="Turbo")
        turbo_lbl.get_style_context().add_class("cpu-form-label")
        off_l = Gtk.Label(label="Off")
        off_l.get_style_context().add_class("turbo-manual-label")
        self.switch = Gtk.Switch()
        self.switch.set_halign(Gtk.Align.CENTER)
        self.switch.set_valign(Gtk.Align.CENTER)
        on_l = Gtk.Label(label="On")
        on_l.get_style_context().add_class("turbo-manual-label")
        self.manual_row.pack_start(turbo_lbl, False, False, 0)
        self.manual_row.pack_start(off_l, False, False, 0)
        self.manual_row.pack_start(self.switch, False, False, 0)
        self.manual_row.pack_start(on_l, False, False, 0)
        self.switch.connect("notify::active", self._on_switch_active)
        self.box.pack_start(self.manual_row, False, False, 0)

        known = {"auto", "never", "always"}
        self.set_current(current if current in known else "auto")

    def _on_auto_clicked(self, _btn: Gtk.Button) -> None:
        if self.current == "auto":
            mode = "always" if self.switch.get_active() else "never"
            self._emit_pick(mode)
        else:
            self._emit_pick("auto")

    def _on_switch_active(self, switch: Gtk.Switch, _pspec) -> None:
        if self._syncing or self.current == "auto":
            return
        mode = "always" if switch.get_active() else "never"
        if mode == self.current:
            return
        self._emit_pick(mode)

    def _emit_pick(self, mode: str) -> None:
        if mode == self.current:
            return
        previous = self.current
        self.set_current(mode)
        self.on_pick(mode, previous)

    def set_current(self, mode: str) -> None:
        if mode not in ("auto", "never", "always"):
            mode = "auto"
        self.current = mode
        ctx = self.auto_btn.get_style_context()
        if mode == "auto":
            ctx.add_class("selected")
        else:
            ctx.remove_class("selected")
        manual_ctx = self.manual_row.get_style_context()
        auto_mode = mode == "auto"
        if auto_mode:
            manual_ctx.add_class("dimmed")
        else:
            manual_ctx.remove_class("dimmed")
        self.switch.set_sensitive(not auto_mode)
        self._syncing = True
        if auto_mode:
            if self._live_turbo is not None:
                self.switch.set_active(self._live_turbo)
        else:
            self.switch.set_active(mode == "always")
        self._syncing = False

    def reflect_live_turbo(self, on: bool | None) -> None:
        if on is None:
            return
        self._live_turbo = on
        if self.current != "auto":
            return
        self._syncing = True
        self.switch.set_active(on)
        self._syncing = False


class RingGauge(Gtk.DrawingArea):
    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(160, 160)
        self.value = 0.0
        self.center = "0%"
        self.sub = ""
        self.connect("draw", self._draw)

    def set_reading(self, value: float, center: str, sub: str = "") -> None:
        self.value = max(0.0, min(100.0, value))
        self.center = center
        self.sub = sub
        self.queue_draw()

    def _draw(self, _widget, cr) -> bool:
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        cx, cy = w / 2, h / 2
        radius = min(w, h) / 2 - 10
        cr.set_line_width(12)
        cr.set_source_rgba(0.12, 0.18, 0.32, 1)
        cr.arc(cx, cy, radius, 0, 2 * math.pi)
        cr.stroke()
        cr.set_source_rgb(0.25, 0.72, 0.95)
        start = -math.pi / 2
        cr.arc(cx, cy, radius, start, start + 2 * math.pi * (self.value / 100.0))
        cr.stroke()
        cr.set_source_rgb(1, 1, 1)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(26)
        ext = cr.text_extents(self.center)
        cr.move_to(cx - ext.width / 2, cy + 4)
        cr.show_text(self.center)
        if self.sub:
            cr.set_source_rgb(0.65, 0.74, 0.88)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(12)
            ext = cr.text_extents(self.sub)
            cr.move_to(cx - ext.width / 2, cy + 24)
            cr.show_text(self.sub)
        return False


class LineChart(Gtk.DrawingArea):
    def __init__(self) -> None:
        super().__init__()
        self.set_size_request(280, 140)
        self.series: list[tuple[tuple[float, float, float], deque]] = []
        self.connect("draw", self._draw)

    def add_series(self, color: tuple[float, float, float], data: deque) -> None:
        self.series.append((color, data))

    def _draw(self, _widget, cr) -> bool:
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        cr.set_source_rgba(0.05, 0.08, 0.16, 1)
        cr.rectangle(0, 0, w, h)
        cr.fill()
        for frac in (0.25, 0.5, 0.75):
            y = h * (1 - frac)
            cr.set_source_rgba(1, 1, 1, 0.06)
            cr.move_to(0, y)
            cr.line_to(w, y)
            cr.stroke()
        for color, data in self.series:
            if len(data) < 2:
                continue
            cr.set_source_rgb(*color)
            cr.set_line_width(2)
            step = w / max(1, len(data) - 1)
            for i, value in enumerate(data):
                x = i * step
                y = h - (max(0.0, min(100.0, value)) / 100.0) * (h - 8) - 4
                if i == 0:
                    cr.move_to(x, y)
                else:
                    cr.line_to(x, y)
            cr.stroke()
        return False


class BatteryIcon(Gtk.DrawingArea):
    """Large battery that fills to the charge level."""

    def __init__(self, width: int = 200, height: int = 300) -> None:
        super().__init__()
        self.set_size_request(width, height)
        self.percent = 0.0
        self.shown = 0.0
        self.charging = False
        self.powersave = False
        self.profile_color: tuple[float, float, float] | None = None
        self.phase = 0.0
        self.connect("draw", self._draw)
        GLib.timeout_add(33, self._animate)

    def set_reading(
        self,
        percent: float | None,
        charging: bool,
        powersave: bool,
        profile_color: tuple[float, float, float] | None = None,
    ) -> None:
        self.percent = 0.0 if percent is None else max(0.0, min(100.0, float(percent)))
        self.charging = charging
        self.powersave = powersave
        self.profile_color = profile_color

    def _animate(self) -> bool:
        self.shown += (self.percent - self.shown) * 0.1
        if abs(self.percent - self.shown) < 0.15:
            self.shown = self.percent
        if self.charging:
            self.phase = (self.phase + 0.012) % 1.0
        self.queue_draw()
        return True

    def _fill_color(self) -> tuple[float, float, float]:
        if self.shown < 20:
            return (0.93, 0.27, 0.32)
        if self.profile_color is not None:
            return self.profile_color
        if self.powersave:
            return (0.96, 0.78, 0.22)
        return (0.20, 0.82, 0.48)

    def _round(self, cr, x, y, w, h, r) -> None:
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _draw(self, _widget, cr) -> bool:
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        nub_h = max(10, int(height * 0.05))
        body_x = width * 0.22
        body_w = width * 0.56
        body_y = nub_h + height * 0.04
        body_h = height - body_y - height * 0.06
        radius = min(18, body_w * 0.18)

        cr.set_source_rgba(0.62, 0.74, 0.92, 0.95)
        nub_w = body_w * 0.36
        self._round(cr, body_x + (body_w - nub_w) / 2, height * 0.02, nub_w, nub_h, 4)
        cr.fill()

        cr.set_source_rgba(0.45, 0.58, 0.82, 0.95)
        cr.set_line_width(4)
        self._round(cr, body_x, body_y, body_w, body_h, radius)
        cr.stroke()

        inset = 9
        ix, iy = body_x + inset, body_y + inset
        iw, ih = body_w - inset * 2, body_h - inset * 2
        fill_h = ih * (self.shown / 100.0)
        fill_y = iy + ih - fill_h
        color = self._fill_color()

        cr.save()
        self._round(cr, ix, iy, iw, ih, max(6, radius - 6))
        cr.clip()
        cr.set_source_rgba(0.08, 0.12, 0.2, 1)
        cr.rectangle(ix, iy, iw, ih)
        cr.fill()
        cr.set_source_rgb(*color)
        cr.rectangle(ix, fill_y, iw, fill_h + 1)
        cr.fill()
        if self.charging and fill_h > 4:
            band = max(18, ih * 0.22)
            travel = ih + band
            y = iy + ih - (self.phase * travel)
            grad = cairo.LinearGradient(0, y, 0, y + band)
            grad.add_color_stop_rgba(0, 1, 1, 1, 0)
            grad.add_color_stop_rgba(0.5, 1, 1, 1, 0.38)
            grad.add_color_stop_rgba(1, 1, 1, 1, 0)
            cr.set_source(grad)
            cr.rectangle(ix, y, iw, band)
            cr.fill()
        cr.restore()

        label = f"{self.shown:.0f}%"
        cr.set_source_rgb(1, 1, 1)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        font_size = max(16, body_w * 0.22)
        cr.set_font_size(font_size)
        ext = cr.text_extents(label)
        baseline = body_y + body_h * 0.58
        cr.move_to(body_x + (body_w - ext.width) / 2, baseline)
        cr.show_text(label)

        if self.charging:
            bolt = min(body_w, body_h) * 0.22
            bolt_cy = baseline + font_size * 0.22 + bolt * 0.62
            bolt_cy = min(bolt_cy, body_y + body_h - bolt * 0.62)
            self._bolt(cr, body_x + body_w / 2, bolt_cy, bolt)
        return False

    def _bolt(self, cr, cx, cy, size) -> None:
        cr.set_source_rgba(1, 1, 1, 0.92)
        cr.move_to(cx + size * 0.08, cy - size * 0.55)
        cr.line_to(cx - size * 0.28, cy + size * 0.05)
        cr.line_to(cx - size * 0.02, cy + size * 0.05)
        cr.line_to(cx - size * 0.12, cy + size * 0.55)
        cr.line_to(cx + size * 0.32, cy - size * 0.08)
        cr.line_to(cx + size * 0.02, cy - size * 0.08)
        cr.close_path()
        cr.fill()


class MemoryBars(Gtk.DrawingArea):
    """RAM, swap, and zram usage as labeled fill bars."""

    def __init__(self, width: int = 360, height: int = 128) -> None:
        super().__init__()
        self.set_size_request(width, height)
        self.ram_pct = self.shown_ram = 0.0
        self.swap_pct = self.shown_swap = 0.0
        self.zram_pct = self.shown_zram = 0.0
        self.ram_used = self.ram_total = 0.0
        self.ram_free = 0.0
        self.swap_used = self.swap_total = 0.0
        self.zram_used = self.zram_total = 0.0
        self.zram_active = False
        self.connect("draw", self._draw)
        GLib.timeout_add(33, self._animate)

    def set_reading(
        self,
        ram_pct: float,
        ram_used: float,
        ram_free: float,
        ram_total: float,
        swap_pct: float,
        swap_used: float,
        swap_total: float,
        zram_pct: float,
        zram_used: float,
        zram_total: float,
        zram_active: bool,
    ) -> None:
        self.ram_pct = max(0.0, min(100.0, ram_pct))
        self.ram_used = ram_used
        self.ram_free = ram_free
        self.ram_total = ram_total
        self.swap_pct = max(0.0, min(100.0, swap_pct))
        self.swap_used = swap_used
        self.swap_total = swap_total
        self.zram_pct = max(0.0, min(100.0, zram_pct))
        self.zram_used = zram_used
        self.zram_total = zram_total
        self.zram_active = zram_active

    def _animate(self) -> bool:
        for target, attr in (
            (self.ram_pct, "shown_ram"),
            (self.swap_pct, "shown_swap"),
            (self.zram_pct, "shown_zram"),
        ):
            current = getattr(self, attr)
            nxt = current + (target - current) * 0.12
            if abs(target - nxt) < 0.2:
                nxt = target
            setattr(self, attr, nxt)
        self.queue_draw()
        return True

    def _round_rect(self, cr, x, y, w, h, r) -> None:
        r = min(r, w / 2, h / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _ram_color(self) -> tuple[float, float, float]:
        if self.shown_ram >= 90:
            return (0.93, 0.35, 0.32)
        if self.shown_ram >= 75:
            return (0.96, 0.62, 0.22)
        return (0.28, 0.72, 0.95)

    def _bar(
        self,
        cr,
        x,
        y,
        w,
        h,
        label: str,
        pct: float,
        fill_rgb: tuple[float, float, float],
        caption: str,
        inactive: bool = False,
    ) -> None:
        cr.set_source_rgb(0.72, 0.78, 0.9)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(11)
        cr.move_to(x, y + 11)
        cr.show_text(label)

        cr.set_source_rgba(0.55, 0.62, 0.78, 0.95)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(10)
        ext = cr.text_extents(caption)
        cr.move_to(x + w - ext.width, y + 11)
        cr.show_text(caption)

        bar_y = y + 16
        bar_h = h - 18
        radius = bar_h / 2
        self._round_rect(cr, x, bar_y, w, bar_h, radius)
        cr.set_source_rgba(0.08, 0.12, 0.22, 1)
        cr.fill()
        self._round_rect(cr, x, bar_y, w, bar_h, radius)
        cr.set_source_rgba(0.35, 0.48, 0.72, 0.35)
        cr.set_line_width(1)
        cr.stroke()

        if inactive:
            cr.set_source_rgba(0.4, 0.45, 0.55, 0.35)
            fill_w = w * 0.08
        else:
            cr.set_source_rgb(*fill_rgb)
            fill_w = max(radius * 2, w * (pct / 100.0))
        if fill_w > 1:
            cr.save()
            self._round_rect(cr, x, bar_y, w, bar_h, radius)
            cr.clip()
            self._round_rect(cr, x, bar_y, fill_w, bar_h, radius)
            cr.fill()
            cr.restore()

    def _draw(self, _widget, cr) -> bool:
        w = self.get_allocated_width()
        h = self.get_allocated_height()
        pad_x = 4
        bar_h = (h - 8) / 3
        ram_cap = f"{self.ram_used:.1f} / {self.ram_total:.1f} GiB  ·  {self.shown_ram:.0f}%  ·  free {self.ram_free:.1f} GiB"
        swap_cap = (
            f"{self.swap_used:.1f} / {self.swap_total:.1f} GiB  ·  {self.shown_swap:.0f}%"
            if self.swap_total > 0.01
            else "No swap"
        )
        if self.zram_active:
            zram_cap = f"{self.zram_used:.1f} / {self.zram_total:.1f} GiB  ·  {self.shown_zram:.0f}%"
        else:
            zram_cap = "Not active"

        self._bar(cr, pad_x, 0, w - pad_x * 2, bar_h, "RAM", self.shown_ram, self._ram_color(), ram_cap)
        self._bar(cr, pad_x, bar_h + 2, w - pad_x * 2, bar_h, "Swap", self.shown_swap, (1.0, 0.62, 0.28), swap_cap)
        self._bar(
            cr,
            pad_x,
            (bar_h + 2) * 2,
            w - pad_x * 2,
            bar_h,
            "Zram",
            self.shown_zram,
            (0.62, 0.42, 0.98),
            zram_cap,
            inactive=not self.zram_active,
        )
        return False


def read_memory_visual_stats() -> dict[str, float | bool]:
    ram_pct = swap_pct = zram_pct = 0.0
    ram_used = ram_free = ram_total = 0.0
    swap_used = swap_total = 0.0
    zram_used = zram_total = 0.0
    zram_active = False
    try:
        import psutil

        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        ram_pct = float(mem.percent)
        ram_used = mem.used / (1024**3)
        ram_free = mem.available / (1024**3)
        ram_total = mem.total / (1024**3)
        swap_total = swap.total / (1024**3)
        swap_used = swap.used / (1024**3)
        swap_pct = float(swap.percent) if swap.total else 0.0
    except Exception:
        pass
    for entry in read_swap_entries():
        if "zram" in str(entry["file"]):
            zram_active = True
            zram_total = entry["size_kb"] / (1024 * 1024)
            zram_used = entry["used_kb"] / (1024 * 1024)
            zram_pct = (entry["used_kb"] / entry["size_kb"] * 100.0) if entry["size_kb"] else 0.0
            break
    return {
        "ram_pct": ram_pct,
        "ram_used": ram_used,
        "ram_free": ram_free,
        "ram_total": ram_total,
        "swap_pct": swap_pct,
        "swap_used": swap_used,
        "swap_total": swap_total,
        "zram_pct": zram_pct,
        "zram_used": zram_used,
        "zram_total": zram_total,
        "zram_active": zram_active,
    }


def _read_file(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def read_live() -> dict:
    info: dict = {
        "cpu_pct": 0.0,
        "freq_ghz": 0.0,
        "temp": None,
        "mem_pct": 0.0,
        "disk_pct": 0.0,
        "net_kbps": 0.0,
        "percent": None,
        "plugged": None,
        "state": "Unknown",
        "health": None,
        "cycles": None,
        "design_wh": None,
        "full_wh": None,
        "rate_w": None,
        "time_left": "—",
        "cpu_name": "CPU",
        "cores": "",
        "product": "This laptop",
    }
    try:
        import psutil

        info["cpu_pct"] = float(psutil.cpu_percent(interval=None))
        info["mem_pct"] = float(psutil.virtual_memory().percent)
        info["disk_pct"] = float(psutil.disk_usage("/").percent)
        temps = psutil.sensors_temperatures()
        if "coretemp" in temps:
            for sensor in temps["coretemp"]:
                if sensor.label and "Package" in sensor.label:
                    info["temp"] = sensor.current
                    break
            if info["temp"] is None and temps["coretemp"]:
                info["temp"] = temps["coretemp"][0].current
        batt = psutil.sensors_battery()
        if batt is not None:
            info["percent"] = batt.percent
            info["plugged"] = bool(batt.power_plugged)
            if batt.secsleft and batt.secsleft > 0:
                hours = int(batt.secsleft // 3600)
                mins = int((batt.secsleft % 3600) // 60)
                info["time_left"] = f"{hours} h {mins} min"
    except Exception:
        pass

    freq = _read_file("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq")
    if freq.isdigit():
        info["freq_ghz"] = int(freq) / 1_000_000
    name = ""
    cores = 0
    threads = 0
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name") and not name:
                name = line.split(":", 1)[1].strip()
            elif line.startswith("cpu cores") and cores == 0:
                cores = int(line.split(":", 1)[1].strip())
            elif line.startswith("processor"):
                threads += 1
    except (OSError, ValueError):
        pass
    info["cpu_name"] = name or "CPU"
    if cores and threads:
        info["cores"] = f"{cores} cores, {threads} threads"
    product = _read_file("/sys/class/dmi/id/product_name")
    vendor = _read_file("/sys/class/dmi/id/sys_vendor")
    info["product"] = " ".join(p for p in (vendor, product) if p) or "This laptop"

    now = time.time()
    cache = getattr(read_live, "_upower", {"t": 0.0, "data": {}})
    if now - cache["t"] > 4:
        data = {}
        try:
            proc = subprocess.run(
                ["upower", "-i", "/org/freedesktop/UPower/devices/battery_BAT1"],
                capture_output=True,
                text=True,
                timeout=4,
            )
            for line in proc.stdout.splitlines():
                if ":" not in line:
                    continue
                key, val = (p.strip() for p in line.split(":", 1))
                data[key] = val
        except (subprocess.TimeoutExpired, FileNotFoundError):
            data = {}
        cache = {"t": now, "data": data}
        read_live._upower = cache
    data = cache["data"]
    if "percentage" in data:
        info["percent"] = float(data["percentage"].rstrip("%"))
    if "state" in data:
        info["state"] = data["state"]
        info["plugged"] = data["state"] == "charging" or "fully" in data["state"]
    if "capacity" in data:
        info["health"] = data["capacity"].split()[0]
    if "charge-cycles" in data:
        info["cycles"] = data["charge-cycles"]
    if "energy-full-design" in data:
        info["design_wh"] = data["energy-full-design"]
    if "energy-full" in data:
        info["full_wh"] = data["energy-full"]
    if "energy-rate" in data:
        try:
            info["rate_w"] = float(data["energy-rate"].split()[0])
        except ValueError:
            pass
    if "time to empty" in data and data["time to empty"] not in ("0 seconds", ""):
        info["time_left"] = data["time to empty"]
    elif "time to full" in data:
        info["time_left"] = data["time to full"]
    return info


ZRAMSWAP_CONF = Path("/etc/default/zramswap")
ZRAMSWAP_BIN = Path("/usr/sbin/zramswap")
SWAPPINESS_SYSCTL = Path("/etc/sysctl.d/99-zorin-cpu-power-swappiness.conf")


def read_swappiness() -> int:
    try:
        return int(Path("/proc/sys/vm/swappiness").read_text().strip())
    except (OSError, ValueError):
        return 60


def read_swap_entries() -> list[dict[str, str | int]]:
    entries: list[dict[str, str | int]] = []
    try:
        lines = Path("/proc/swaps").read_text().splitlines()
    except OSError:
        return entries
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        try:
            entries.append(
                {
                    "file": parts[0],
                    "type": parts[1],
                    "size_kb": int(parts[2]),
                    "used_kb": int(parts[3]),
                    "prio": parts[4],
                }
            )
        except ValueError:
            continue
    return entries


def format_swap_list() -> str:
    rows: list[str] = []
    for entry in read_swap_entries():
        size_g = entry["size_kb"] / (1024 * 1024)
        used_g = entry["used_kb"] / (1024 * 1024)
        name = str(entry["file"]).replace("/dev/", "")
        rows.append(
            f"{name}  ({entry['type']})  {used_g:.1f} / {size_g:.1f} GiB used  ·  priority {entry['prio']}"
        )
    return "\n".join(rows) if rows else "No swap devices active."


def zram_tools_available() -> bool:
    return ZRAMSWAP_CONF.is_file() and ZRAMSWAP_BIN.is_file()


def read_zramswap_conf() -> dict[str, int | str | bool]:
    conf: dict[str, int | str | bool] = {
        "algo": "lz4",
        "percent": 40,
        "priority": 100,
        "available": zram_tools_available(),
    }
    if not ZRAMSWAP_CONF.is_file():
        return conf
    try:
        for line in ZRAMSWAP_CONF.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = (p.strip() for p in line.split("=", 1))
            if key == "ALGO" and val:
                conf["algo"] = val
            elif key == "PERCENT" and val.isdigit():
                conf["percent"] = int(val)
            elif key == "PRIORITY" and val.lstrip("-").isdigit():
                conf["priority"] = int(val)
    except OSError:
        pass
    return conf


def read_zram_comp_algorithms() -> tuple[list[str], str]:
    path = Path("/sys/block/zram0/comp_algorithm")
    if not path.is_file():
        return ["lz4", "lzo", "zstd"], str(read_zramswap_conf()["algo"])
    raw = path.read_text().strip()
    current = raw
    algos: list[str] = []
    for token in raw.split():
        if token.startswith("[") and token.endswith("]"):
            current = token[1:-1]
            algos.append(current)
        else:
            algos.append(token)
    if not algos:
        algos = ["lz4", "lzo", "zstd"]
    return algos, current


def is_zramswap_boot_enabled() -> bool:
    try:
        proc = subprocess.run(
            ["systemctl", "is-enabled", "zramswap.service"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return proc.returncode == 0 and proc.stdout.strip() == "enabled"


def read_memory_overview() -> dict[str, str]:
    ram_used = ram_total = mem_pct = 0.0
    swap_used = swap_total = 0.0
    try:
        import psutil

        mem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        ram_used = mem.used / (1024**3)
        ram_total = mem.total / (1024**3)
        mem_pct = mem.percent
        swap_used = swap.used / (1024**3)
        swap_total = swap.total / (1024**3)
    except Exception:
        pass
    zram_line = "Zram  not active"
    for entry in read_swap_entries():
        if "zram" in str(entry["file"]):
            used_g = entry["used_kb"] / (1024 * 1024)
            size_g = entry["size_kb"] / (1024 * 1024)
            zram_line = f"Zram  {used_g:.1f} / {size_g:.1f} GiB swap used"
            break
    return {
        "ram": f"RAM  {ram_used:.1f} / {ram_total:.1f} GiB used ({mem_pct:.0f}%)",
        "swap": f"Swap (all)  {swap_used:.1f} / {swap_total:.1f} GiB used",
        "zram": zram_line,
        "swappiness": f"Swappiness  {read_swappiness()} (higher uses swap sooner)",
        "swap_list": format_swap_list(),
    }


def run_set_swappiness(value: int) -> tuple[bool, str]:
    if value < 0 or value > 200:
        return False, "Swappiness must be between 0 and 200."
    ok, msg = run_pkexec(["set-swappiness", str(value)])
    if ok:
        return True, f"Swappiness set to {value} (saved for reboot)."
    return ok, msg


def run_apply_zramswap(algo: str, percent: int, priority: int) -> tuple[bool, str]:
    if not zram_tools_available():
        return False, "zram-tools is not installed (/etc/default/zramswap missing)."
    if percent < 5 or percent > 100:
        return False, "Zram size percent must be between 5 and 100."
    if not algo.replace("_", "").replace("-", "").isalnum():
        return False, "Invalid compression algorithm name."
    body = f"ALGO={algo}\nPERCENT={percent}\nPRIORITY={priority}\n"
    with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
        tmp.write(body)
        tmp_path = tmp.name
    ok, msg = run_pkexec(["write-zramswap", tmp_path])
    Path(tmp_path).unlink(missing_ok=True)
    if ok:
        return True, "Zram settings applied and zramswap restarted."
    return ok, msg


def run_zramswap_boot(enable: bool) -> tuple[bool, str]:
    if not zram_tools_available():
        return False, "zram-tools is not installed."
    ok, msg = run_pkexec(["zramswap-boot", "enable" if enable else "disable"])
    if ok:
        return True, ("Zram enabled at boot." if enable else "Zram disabled (other swap unchanged).")
    return ok, msg


SCHEDULE_PATH = Path.home() / ".config/zorin-cpu-power/schedules.json"


def load_schedule() -> dict:
    default = {
        "charger_on": False,
        "charger_rule": "adaptive",
        "battery_on": False,
        "battery_rule": "powersave",
        "low_on": False,
        "low_rule": "silent",
        "low_pct": 20,
    }
    try:
        saved = json.loads(SCHEDULE_PATH.read_text())
        default["charger_on"] = bool(saved.get("charger_on", False))
        default["battery_on"] = bool(saved.get("battery_on", False))
        default["low_on"] = bool(saved.get("low_on", False))
        default["low_pct"] = int(saved.get("low_pct", 20) or 20)
        default["charger_rule"] = normalize_power_rule(saved.get("charger_rule") or saved.get("charger_mode"), "adaptive")
        default["battery_rule"] = normalize_power_rule(saved.get("battery_rule") or saved.get("battery_mode"), "powersave")
        default["low_rule"] = normalize_power_rule(saved.get("low_rule") or saved.get("low_mode"), "silent")
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return default


def save_schedule(data: dict) -> None:
    SCHEDULE_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULE_PATH.write_text(json.dumps(data, indent=2))


def find_kbd_led() -> tuple[str, Path] | None:
    root = Path("/sys/class/leds")
    if not root.is_dir():
        return None
    candidates: list[tuple[str, Path]] = []
    for path in sorted(root.iterdir()):
        if "kbd_backlight" in path.name:
            candidates.append((path.name, path))
    if not candidates:
        return None
    for prefer in ("samsung-galaxybook", "galaxybook"):
        for name, path in candidates:
            if prefer in name:
                return name, path
    return candidates[0]


def kbd_level_name(level: int, maximum: int) -> str:
    if level <= 0:
        return "Off"
    if maximum <= 1 or level >= maximum:
        return "High" if maximum > 1 else "On"
    if level == 1:
        return "Low"
    return "Medium"


def kbd_timeout_name(seconds: int) -> str:
    for value, label in KBD_TIMEOUTS:
        if value == seconds:
            return label
    if seconds <= 0:
        return "Always on"
    if seconds < 60:
        return f"{seconds} sec"
    return f"{seconds // 60} min"


class KeyboardLight:
    """Backlight brightness plus an idle timer.

    Brightness is written through the login session, which is allowed to
    change the Samsung keyboard LED. The timer uses the GNOME idle clock,
    so the light turns off after no keyboard or mouse use and comes back
    on the next use. The chosen brightness is remembered while it is off.
    """

    def __init__(self) -> None:
        found = find_kbd_led()
        self.led_name = found[0] if found else ""
        self.led_path = found[1] if found else None
        self.max_brightness = self._read_max()
        self.level = 0
        self.timeout_sec = 30
        self._blanked = False
        self._error = ""
        self._hold_until = 0.0
        self._listeners: list = []
        self._idle_proxy = None
        self._brightness_proxy = None
        self._brightness_monitor = None
        self._load()
        if self.available:
            self._watch_brightness()
        if self.available and self._read_brightness() != (0 if self._blanked else self.level):
            self._write(self.level)
        GLib.timeout_add_seconds(1, self._poll)

    @property
    def available(self) -> bool:
        return self.led_path is not None and self.max_brightness > 0

    def add_listener(self, callback) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def set_level(self, level: int) -> None:
        if not self.available:
            return
        level = max(0, min(self.max_brightness, int(level)))
        self._blanked = False
        ok, err = self._write(level)
        if ok:
            self.level = level
            self._error = ""
            self._save()
        else:
            self._error = err or "Could not change the keyboard backlight."
        self._emit()

    def refresh_from_hardware(self) -> None:
        if not self.available:
            return
        current = self._read_brightness()
        if current is None:
            return
        if self._blanked and self.level > 0 and current == 0:
            return
        if current > 0:
            self.level = min(self.max_brightness, current)
            self._blanked = False
        elif not self._blanked:
            self.level = 0
        self._save()
        self._emit()

    def set_timeout(self, seconds: int) -> None:
        self.timeout_sec = max(0, int(seconds))
        self._save()
        if self.timeout_sec <= 0 and self._blanked and self.level > 0:
            self._blanked = False
            ok, err = self._write(self.level)
            self._error = "" if ok else err
        self._emit()

    def status_line(self) -> str:
        if not self.available:
            return "No keyboard backlight was found."
        if self._error:
            return self._error
        name = kbd_level_name(self.level, self.max_brightness)
        if self.level <= 0:
            return "Backlight is off."
        if self._blanked:
            return f"Off until you use the computer. It returns to {name}."
        if self.timeout_sec <= 0:
            return f"{name}. Stays on."
        return f"{name}. Turns off after {kbd_timeout_name(self.timeout_sec).lower()} of no use."

    def _emit(self) -> None:
        for callback in list(self._listeners):
            callback()

    def _load(self) -> None:
        current = self._read_brightness()
        self.level = current if current and current > 0 else min(self.max_brightness, 3) or 0
        self.timeout_sec = 30
        try:
            data = json.loads(KBD_CONF.read_text())
        except (OSError, json.JSONDecodeError, TypeError):
            data = None
        if isinstance(data, dict):
            try:
                self.level = int(data.get("brightness", self.level))
            except (TypeError, ValueError):
                pass
            try:
                self.timeout_sec = int(data.get("timeout", self.timeout_sec))
            except (TypeError, ValueError):
                pass
        self.level = max(0, min(self.max_brightness or 0, self.level))
        self.timeout_sec = max(0, self.timeout_sec)

    def _save(self) -> None:
        try:
            KBD_CONF.parent.mkdir(parents=True, exist_ok=True)
            KBD_CONF.write_text(
                json.dumps({"brightness": self.level, "timeout": self.timeout_sec}, indent=2)
            )
        except OSError:
            pass

    def _read_max(self) -> int:
        if self.led_path is None:
            return 0
        try:
            return max(0, int((self.led_path / "max_brightness").read_text().strip()))
        except (OSError, ValueError):
            return 0

    def _read_brightness(self) -> int | None:
        if self.led_path is None:
            return None
        try:
            return int((self.led_path / "brightness").read_text().strip())
        except (OSError, ValueError):
            return None

    def _brightness_proxy_get(self):
        if self._brightness_proxy is None:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            self._brightness_proxy = Gio.DBusProxy.new_sync(
                bus,
                Gio.DBusProxyFlags.NONE,
                None,
                "org.freedesktop.login1",
                "/org/freedesktop/login1/session/auto",
                "org.freedesktop.login1.Session",
                None,
            )
        return self._brightness_proxy

    def _write(self, level: int) -> tuple[bool, str]:
        if not self.available:
            return False, "No keyboard backlight was found."
        level = max(0, min(self.max_brightness, int(level)))
        last_err = ""
        for attempt in range(2):
            try:
                self._brightness_proxy_get().call_sync(
                    "SetBrightness",
                    GLib.Variant("(ssu)", ("leds", self.led_name, level)),
                    Gio.DBusCallFlags.NONE,
                    5000,
                    None,
                )
                self._hold_until = time.monotonic() + 1.2
                return True, ""
            except GLib.Error as exc:
                last_err = exc.message or "Could not change the keyboard backlight."
                self._brightness_proxy = None
        return False, last_err

    def _watch_brightness(self) -> None:
        if self.led_path is None or self._brightness_monitor is not None:
            return
        brightness_file = self.led_path / "brightness"
        if not brightness_file.is_file():
            return
        try:
            gfile = Gio.File.new_for_path(str(brightness_file))
            self._brightness_monitor = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
            self._brightness_monitor.connect("changed", self._on_brightness_file_changed)
        except GLib.Error:
            self._brightness_monitor = None

    def _on_brightness_file_changed(self, *_args) -> None:
        if time.monotonic() < self._hold_until:
            return
        current = self._read_brightness()
        if current is None:
            return
        expected = 0 if self._blanked else self.level
        if current == expected:
            return
        if current > 0:
            self.level = min(self.max_brightness, current)
            self._blanked = False
        elif not self._blanked:
            self.level = 0
        self._save()
        self._emit()

    def _idletime_ms(self) -> int | None:
        try:
            if self._idle_proxy is None:
                bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                self._idle_proxy = Gio.DBusProxy.new_sync(
                    bus,
                    Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES,
                    None,
                    "org.gnome.Shell",
                    "/org/gnome/Mutter/IdleMonitor/Core",
                    "org.gnome.Mutter.IdleMonitor",
                    None,
                )
            result = self._idle_proxy.call_sync(
                "GetIdletime",
                None,
                Gio.DBusCallFlags.NONE,
                1000,
                None,
            )
            return int(result.unpack()[0])
        except (GLib.Error, TypeError, ValueError, IndexError):
            self._idle_proxy = None
            return None

    def _poll(self) -> bool:
        if not self.available:
            return True
        self._sync_hardware()
        if self.level <= 0 or self.timeout_sec <= 0:
            if self._blanked:
                self._blanked = False
                if self.level > 0:
                    ok, err = self._write(self.level)
                    self._error = "" if ok else err
                self._emit()
            return True
        idle = self._idletime_ms()
        if idle is None:
            if not self._error:
                self._error = "Backlight timer needs the GNOME session."
                self._emit()
            return True
        if self._error == "Backlight timer needs the GNOME session.":
            self._error = ""
        if idle >= self.timeout_sec * 1000:
            if not self._blanked:
                ok, err = self._write(0)
                if ok:
                    self._blanked = True
                    self._error = ""
                else:
                    self._error = err
                self._emit()
        elif self._blanked:
            self._blanked = False
            ok, err = self._write(self.level)
            self._error = "" if ok else err
            self._emit()
        return True

    def _sync_hardware(self) -> None:
        if time.monotonic() < self._hold_until:
            return
        current = self._read_brightness()
        if current is None:
            return
        expected = 0 if self._blanked else self.level
        if current == expected:
            return
        if current > 0:
            self.level = min(self.max_brightness, current)
            self._blanked = False
        elif not self._blanked:
            self.level = 0
        self._save()
        self._emit()


_KEYBOARD: KeyboardLight | None = None


def get_keyboard() -> KeyboardLight:
    global _KEYBOARD
    if _KEYBOARD is None:
        _KEYBOARD = KeyboardLight()
    return _KEYBOARD


def load_smooth_brightness_config() -> dict:
    cfg = dict(SMOOTH_BRIGHTNESS_DEFAULTS)
    try:
        data = json.loads(SMOOTH_BRIGHTNESS_CONF.read_text())
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
    except (TypeError, ValueError):
        cfg = dict(SMOOTH_BRIGHTNESS_DEFAULTS)
    cfg["ambient_bandwidth_hz"] = max(0.005, min(0.2, cfg["ambient_bandwidth_hz"]))
    cfg["brightness_time_constant_sec"] = max(1.0, min(30.0, cfg["brightness_time_constant_sec"]))
    cfg["tick_ms"] = max(50, min(500, cfg["tick_ms"]))
    return cfg


def save_smooth_brightness_config(data: dict) -> None:
    cfg = load_smooth_brightness_config()
    cfg["ambient_bandwidth_hz"] = max(
        0.005, min(0.2, float(data.get("ambient_bandwidth_hz", cfg["ambient_bandwidth_hz"])))
    )
    cfg["brightness_time_constant_sec"] = max(
        1.0, min(30.0, float(data.get("brightness_time_constant_sec", cfg["brightness_time_constant_sec"])))
    )
    cfg["tick_ms"] = max(50, min(500, int(data.get("tick_ms", cfg["tick_ms"]))))
    SMOOTH_BRIGHTNESS_CONF.parent.mkdir(parents=True, exist_ok=True)
    SMOOTH_BRIGHTNESS_CONF.write_text(json.dumps(cfg, indent=2))


def read_ambient_brightness_enabled() -> bool:
    try:
        settings = Gio.Settings.new("org.gnome.settings-daemon.plugins.power")
        return settings.get_boolean("ambient-enabled")
    except GLib.Error:
        return False


def set_ambient_brightness_enabled(enabled: bool) -> bool:
    try:
        settings = Gio.Settings.new("org.gnome.settings-daemon.plugins.power")
        settings.set_boolean("ambient-enabled", enabled)
        return True
    except GLib.Error:
        return False


def smooth_brightness_service_active() -> bool:
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "is-active", "smooth-adaptive-brightness.service"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False
    return proc.stdout.strip() == "active"


def restart_smooth_brightness_service() -> tuple[bool, str]:
    if not SMOOTH_BRIGHTNESS_BIN.is_file():
        return False, "Smooth brightness helper is not installed yet."
    try:
        proc = subprocess.run(
            ["systemctl", "--user", "restart", "smooth-adaptive-brightness.service"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, "Could not run systemctl."
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        return False, msg or "Failed to restart the smooth brightness service."
    return True, "Smooth brightness settings applied."


def smooth_brightness_install_script() -> Path | None:
    here = Path(__file__).resolve().parent
    for base in (here, Path.home() / "Projects/zorin-cpu-power"):
        script = base / "smooth-adaptive-brightness" / "install.sh"
        if script.is_file():
            return script
    share = Path.home() / ".local/share/zorin-cpu-power/smooth-adaptive-brightness/install.sh"
    if share.is_file():
        return share
    return None


def install_smooth_brightness_service() -> tuple[bool, str]:
    script = smooth_brightness_install_script()
    if script is None:
        return False, "Install script for smooth brightness was not found."
    try:
        proc = subprocess.run(
            ["/bin/bash", str(script)],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(script.parent),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False, "Install timed out."
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        return False, out or "Install failed."
    return True, out or "Smooth brightness service installed."


def format_smooth_brightness_status() -> str:
    parts = []
    if smooth_brightness_service_active():
        parts.append("Smooth brightness service: running")
    elif SMOOTH_BRIGHTNESS_BIN.is_file():
        parts.append("Smooth brightness service: not running")
    else:
        parts.append("Smooth brightness service: not installed")
    parts.append(
        "Automatic brightness (Settings): "
        + ("on" if read_ambient_brightness_enabled() else "off")
    )
    return "   ·   ".join(parts)


class ControlWindow(Gtk.Window):
    NAV = (
        ("Dashboard", "dashboard"),
        ("CPU", "cpu"),
        ("Battery", "battery"),
        ("Display · Beta", "display"),
        ("Keyboard", "keyboard"),
        ("Memory", "memory"),
        ("Scheduler", "scheduler"),
        ("Monitor", "monitor"),
        ("Settings", "settings"),
    )

    def __init__(self, on_changed) -> None:
        super().__init__(title="CPU Power")
        self.set_default_size(1100, 760)
        self.get_style_context().add_class("cpubg")
        self.on_changed = on_changed
        if LOCAL_ICON.is_file():
            self.set_icon_from_file(str(LOCAL_ICON))
        elif ICON_PATH.is_file():
            self.set_icon_from_file(str(ICON_PATH))

        self._updating = False
        self._nav_buttons: dict[str, Gtk.Button] = {}
        self._preset_buttons: dict[int, Gtk.Button] = {}
        self.ui: dict[str, Gtk.Label] = {}
        self.live = read_live()
        self.schedule = load_schedule()
        self.power_rules = load_power_rules()
        self._under_schedule = False
        self._sched_last: str | None = None
        self._net_prev = None
        self.hist_cpu: deque = deque(maxlen=40)
        self.hist_temp: deque = deque(maxlen=40)
        self.hist_watt: deque = deque(maxlen=40)
        self.hist_mem: deque = deque(maxlen=40)

        self.toast = Gtk.Label(label="")
        self.toast.get_style_context().add_class("cpu-toast")
        self.toast.set_halign(Gtk.Align.START)
        self.toast.set_line_wrap(True)

        self.daemon_banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.daemon_banner.get_style_context().add_class("cpu-daemon-banner")
        self.daemon_banner.set_no_show_all(True)
        banner_text = Gtk.Label()
        banner_text.get_style_context().add_class("cpu-daemon-banner-text")
        banner_text.set_markup(
            "<b>auto-cpufreq daemon is not running.</b> "
            "Profile changes will fail until it is repaired."
        )
        banner_text.set_line_wrap(True)
        banner_text.set_halign(Gtk.Align.START)
        self.daemon_install_btn = Gtk.Button(label="Repair and start daemon")
        self.daemon_install_btn.get_style_context().add_class("cpu-apply-btn")
        self.daemon_install_btn.connect("clicked", self._on_install_daemon)
        self.daemon_banner.pack_start(banner_text, True, True, 0)
        self.daemon_banner.pack_start(self.daemon_install_btn, False, False, 0)

        self.profile_tiles = ProfileTiles(read_samsung_mode(), self._on_profile_pick)
        self._plat_last = read_platform_profile()
        self._plat_cool = False
        self._plat_ts = 0.0
        self._tick_id = 0
        self._adaptive_armed = False
        self.turbo_choice = TurboControl(read_turbo_override(), self._on_turbo_pick)
        self.gauge = RingGauge()
        self.chart = LineChart()
        self.chart.add_series((0.35, 0.75, 1.0), self.hist_cpu)
        self.chart.add_series((0.65, 0.45, 1.0), self.hist_temp)
        self.chart.add_series((1.0, 0.65, 0.3), self.hist_watt)
        self.mem_chart = LineChart()
        self.mem_chart.add_series((0.35, 0.9, 0.7), self.hist_mem)

        conf = read_auto_cpufreq_conf()
        self.charger_gov = _combo_row(
            "On charger — governor", GOVERNORS, conf["charger"]["governor"], GOVERNOR_LABELS
        )
        self.charger_turbo = _combo_row(
            "On charger — turbo", TURBO_MODES, conf["charger"]["turbo"], TURBO_LABELS
        )
        self.battery_gov = _combo_row(
            "On battery — governor", GOVERNORS, conf["battery"]["governor"], GOVERNOR_LABELS
        )
        self.battery_turbo = _combo_row(
            "On battery — turbo", TURBO_MODES, conf["battery"]["turbo"], TURBO_LABELS
        )
        for pair in (self.charger_gov, self.charger_turbo, self.battery_gov, self.battery_turbo):
            pair[0].get_style_context().add_class("cpu-form-label")
            pair[1].get_style_context().add_class("cpu-combo")
            pair[1].connect("changed", self._on_save_conf)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(160)
        for _label, key in self.NAV:
            self.stack.add_named(getattr(self, f"_page_{key}")(), key)

        scroll_host = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll_host.set_border_width(8)
        scroll_host.pack_start(self.daemon_banner, False, False, 0)
        scroll_host.pack_start(self.toast, False, False, 0)
        scroll_host.pack_start(self.stack, True, True, 0)

        self.status_profile = Gtk.Label()
        self.status_turbo = Gtk.Label()
        self.status_charge = Gtk.Label()
        self.status_gov_c = Gtk.Label()
        self.status_gov_b = Gtk.Label()
        statusbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        statusbar.get_style_context().add_class("cpu-statusbar")
        for lbl in (
            self.status_profile,
            self.status_turbo,
            self.status_charge,
            self.status_gov_c,
            self.status_gov_b,
        ):
            lbl.get_style_context().add_class("cpu-status-chip")
            statusbar.pack_start(lbl, False, False, 0)

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main.pack_start(scroll_host, True, True, 0)
        main.pack_start(statusbar, False, False, 0)

        root = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        root.pack_start(self._build_sidebar(), False, False, 0)
        root.pack_start(main, True, True, 0)
        self.add(root)
        self.connect("delete-event", self._on_delete_hide)
        self.show_all()
        self._show_page("dashboard")
        self.refresh_status()
        self._sync_daemon_banner(force=not is_auto_cpufreq_daemon_active())
        self._start_live_updates()

    def _on_delete_hide(self, *_args) -> bool:
        self.hide()
        return True

    def _start_live_updates(self) -> None:
        if self._tick_id:
            return
        self._adaptive_armed = False
        self._tick_id = GLib.timeout_add_seconds(2, self._tick)

    def _stop_live_updates(self) -> None:
        if self._tick_id:
            GLib.source_remove(self._tick_id)
            self._tick_id = 0
        self._adaptive_armed = False

    def _build_sidebar(self) -> Gtk.Box:
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        side.set_size_request(210, -1)
        side.get_style_context().add_class("cpu-sidebar")
        side.set_border_width(14)
        title = Gtk.Label(label="Power Control\nfor Linux")
        title.get_style_context().add_class("cpu-sidebar-title")
        title.set_halign(Gtk.Align.START)
        title.set_justify(Gtk.Justification.LEFT)
        side.pack_start(title, False, False, 0)
        for label, key in self.NAV:
            btn = Gtk.Button(label=label)
            btn.get_style_context().add_class("cpu-nav-btn")
            btn.connect("clicked", lambda _b, k=key: self._show_page(k))
            side.pack_start(btn, False, False, 0)
            self._nav_buttons[key] = btn
        side.pack_start(Gtk.Box(), True, True, 0)
        foot = Gtk.Label(label="")
        foot.get_style_context().add_class("cpu-sidebar-footer")
        foot.set_line_wrap(True)
        foot.set_justify(Gtk.Justification.CENTER)
        self.ui["sidebar_foot"] = foot
        side.pack_start(foot, False, False, 0)
        return side

    def _show_page(self, key: str) -> None:
        self.stack.set_visible_child_name(key)
        for name, btn in self._nav_buttons.items():
            ctx = btn.get_style_context()
            if name == key:
                ctx.add_class("active")
            else:
                ctx.remove_class("active")
        if key == "keyboard" and hasattr(self, "kbd_scale"):
            get_keyboard().refresh_from_hardware()
            self._sync_kbd_ui()
        if key == "display" and hasattr(self, "display_status"):
            self._sync_display_ui()

    def _sync_display_ui(self) -> None:
        if not hasattr(self, "display_status"):
            return
        cfg = load_smooth_brightness_config()
        self._updating = True
        try:
            self.display_ambient.set_active(read_ambient_brightness_enabled())
            self.display_fade.set_value(cfg["brightness_time_constant_sec"])
            self.display_fade_val.set_text(f"{cfg['brightness_time_constant_sec']:.1f} s")
            self.display_ambient_smooth.set_value(cfg["ambient_bandwidth_hz"])
            self.display_ambient_val.set_text(f"{cfg['ambient_bandwidth_hz']:.3f} Hz")
            self.display_status.set_text(format_smooth_brightness_status())
            installed = SMOOTH_BRIGHTNESS_BIN.is_file()
            self.display_install_btn.set_visible(not installed)
            self.display_apply_btn.set_sensitive(installed)
        finally:
            self._updating = False

    def _on_display_ambient(self, switch: Gtk.Switch, _pspec) -> None:
        if self._updating:
            return
        if not set_ambient_brightness_enabled(switch.get_active()):
            self._updating = True
            switch.set_active(not switch.get_active())
            self._updating = False
            self._set_status("Could not change automatic brightness in Settings.")
            return
        self._sync_display_ui()

    def _on_display_fade(self, scale: Gtk.Scale) -> None:
        if self._updating:
            return
        self.display_fade_val.set_text(f"{scale.get_value():.1f} s")

    def _on_display_ambient_smooth(self, scale: Gtk.Scale) -> None:
        if self._updating:
            return
        self.display_ambient_val.set_text(f"{scale.get_value():.3f} Hz")

    def _on_apply_display_smooth(self, _btn: Gtk.Button) -> None:
        save_smooth_brightness_config({
            "brightness_time_constant_sec": self.display_fade.get_value(),
            "ambient_bandwidth_hz": self.display_ambient_smooth.get_value(),
            "tick_ms": load_smooth_brightness_config()["tick_ms"],
        })
        ok, msg = restart_smooth_brightness_service()
        self._sync_display_ui()
        self._set_status(msg)

    def _on_install_display_smooth(self, _btn: Gtk.Button) -> None:
        self.display_install_btn.set_sensitive(False)
        self._set_status("Installing smooth brightness service…")

        def runner() -> None:
            result = install_smooth_brightness_service()

            def finish() -> bool:
                self.display_install_btn.set_sensitive(True)
                self._sync_display_ui()
                self._set_status(result[1])
                return False

            GLib.idle_add(finish)

        threading.Thread(target=runner, daemon=True).start()

    def _page_shell(self, title: str, subtitle: str) -> tuple[Gtk.ScrolledWindow, Gtk.Box]:
        outer = Gtk.ScrolledWindow()
        outer.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_border_width(16)
        heading = Gtk.Label(label=title)
        heading.get_style_context().add_class("page-title")
        heading.set_halign(Gtk.Align.START)
        sub = Gtk.Label(label=subtitle)
        sub.get_style_context().add_class("cpu-hero-sub")
        sub.set_halign(Gtk.Align.START)
        sub.set_line_wrap(True)
        box.pack_start(heading, False, False, 0)
        box.pack_start(sub, False, False, 0)
        outer.add(box)
        return outer, box

    def _stat_card(self, title: str, key: str) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card.get_style_context().add_class("cpu-card")
        card.set_border_width(14)
        cap = Gtk.Label(label=title)
        cap.get_style_context().add_class("cpu-card-desc")
        cap.set_halign(Gtk.Align.START)
        val = Gtk.Label(label="—")
        val.get_style_context().add_class("cpu-stat-value")
        val.set_halign(Gtk.Align.START)
        self.ui[key] = val
        card.pack_start(cap, False, False, 0)
        card.pack_start(val, False, False, 0)
        return card

    def _bind(self, key: str, text: str = "—") -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_halign(Gtk.Align.START)
        lbl.set_line_wrap(True)
        self.ui[key] = lbl
        return lbl

    def _page_dashboard(self) -> Gtk.Widget:
        outer, box = self._page_shell("Dashboard", "Overview of performance, power, and battery.")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        for title, key in (
            ("Active profile", "dash_profile"),
            ("Turbo boost", "dash_turbo"),
            ("Charge limit", "dash_limit"),
            ("Power state", "dash_state"),
        ):
            row.pack_start(self._stat_card(title, key), True, True, 0)
        box.pack_start(row, False, False, 0)

        mid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        cpu_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        cpu_card.get_style_context().add_class("cpu-card")
        cpu_card.set_border_width(14)
        cpu_card.pack_start(self._bind("cpu_title", "CPU"), False, False, 0)
        self.ui["cpu_title"].get_style_context().add_class("cpu-card-title")
        cpu_card.pack_start(self._bind("cpu_sub", ""), False, False, 0)
        self.ui["cpu_sub"].get_style_context().add_class("cpu-card-desc")
        cpu_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        cpu_row.pack_start(self.gauge, False, False, 0)
        facts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        facts.pack_start(self._bind("freq", "Frequency —"), False, False, 0)
        facts.pack_start(self._bind("temp", "Temperature —"), False, False, 0)
        facts.pack_start(self._bind("watts", "System power —"), False, False, 0)
        cpu_row.pack_start(facts, True, True, 0)
        cpu_card.pack_start(cpu_row, False, False, 0)
        self.dash_memory = MemoryBars(380, 132)
        cpu_card.pack_start(self.dash_memory, False, False, 0)
        mid.pack_start(cpu_card, True, True, 0)

        batt_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        batt_card.get_style_context().add_class("cpu-card")
        batt_card.set_border_width(14)
        batt_card.pack_start(self._bind("batt_big", "Battery"), False, False, 0)
        self.ui["batt_big"].get_style_context().add_class("cpu-stat-value")
        self.dash_battery = BatteryIcon(140, 200)
        batt_card.pack_start(self.dash_battery, False, False, 0)
        for key, label in (
            ("health", "Health"),
            ("cycles", "Cycles"),
            ("design", "Design capacity"),
            ("full", "Full capacity"),
            ("time_left", "Time remaining"),
            ("rate", "Discharge / charge rate"),
        ):
            batt_card.pack_start(self._bind(key, label), False, False, 0)
        mid.pack_start(batt_card, True, True, 0)
        box.pack_start(mid, False, False, 0)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for label, kind, mode in (
            ("Adaptive", "profile", "adaptive"),
            ("Silent", "profile", "silent"),
            ("Quiet", "profile", "quiet"),
            ("Balance", "profile", "balance"),
            ("Power", "profile", "power"),
            ("Turbo on", "turbo", "always"),
            ("Charge limit", "page", "battery"),
            ("Auto rules", "page", "cpu"),
        ):
            btn = Gtk.Button(label=label)
            btn.get_style_context().add_class("cpu-quick")
            if kind == "profile":
                btn.connect("clicked", lambda _b, m=mode: self._quick_profile(m))
                self.ui[f"quick_{mode}"] = btn
            elif kind == "turbo":
                btn.connect("clicked", lambda _b: self._quick_turbo("always"))
            else:
                btn.connect("clicked", lambda _b, k=mode: self._show_page(k))
            actions.pack_start(btn, True, True, 0)
        box.pack_start(actions, False, False, 0)

        lower = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        chart_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        chart_card.get_style_context().add_class("cpu-card")
        chart_card.set_border_width(14)
        cap = Gtk.Label(label="Power and performance  ·  usage, temperature, system watts")
        cap.get_style_context().add_class("cpu-card-desc")
        cap.set_halign(Gtk.Align.START)
        chart_card.pack_start(cap, False, False, 0)
        chart_card.pack_start(self.chart, True, True, 0)
        lower.pack_start(chart_card, True, True, 0)
        summary = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        summary.get_style_context().add_class("cpu-card")
        summary.set_border_width(14)
        summary.pack_start(self._bind("sum_title", "System summary"), False, False, 0)
        self.ui["sum_title"].get_style_context().add_class("cpu-card-title")
        for key in ("sum_gov_ac", "sum_gov_bat", "sum_turbo_ac", "sum_turbo_bat"):
            summary.pack_start(self._bind(key), False, False, 0)
        lower.pack_start(summary, True, True, 0)
        box.pack_start(lower, True, True, 0)
        return outer

    def _page_cpu(self) -> Gtk.Widget:
        outer, box = self._page_shell(
            "CPU",
            "Samsung Silent, Quiet, Balance, and Power, plus Adaptive. At idle the fan is already stopped; Quiet and Balance change the fan once the CPU warms up.",
        )
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.get_style_context().add_class("cpu-card")
        card.set_border_width(14)
        card.pack_start(self._bind("cpu_page_line", ""), False, False, 0)
        card.pack_start(self._bind("plat_now", ""), False, False, 0)
        card.pack_start(Gtk.Label(label="Profile", halign=Gtk.Align.START), False, False, 0)
        card.pack_start(self.profile_tiles.box, False, False, 0)
        hint = Gtk.Label(
            label="Adaptive follows the rules below. Silent, Quiet, Balance, and Power stay on until you pick Adaptive again.",
            wrap=True,
            halign=Gtk.Align.START,
        )
        hint.get_style_context().add_class("cpu-card-desc")
        card.pack_start(hint, False, False, 0)
        box.pack_start(card, False, False, 0)
        box.pack_start(self._rules_card(), False, False, 0)
        turbo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        turbo.get_style_context().add_class("cpu-card")
        turbo.set_border_width(14)
        turbo.pack_start(Gtk.Label(label="Turbo boost", halign=Gtk.Align.START), False, False, 0)
        note = Gtk.Label(
            label="Auto keeps turbo on while the CPU is busy. It turns turbo off when the laptop is idle, and on AC power also when it is warm and idle (about 65–70°C).",
            wrap=True,
            halign=Gtk.Align.START,
        )
        note.get_style_context().add_class("cpu-card-desc")
        turbo.pack_start(note, False, False, 0)
        turbo.pack_start(self.turbo_choice.box, False, False, 0)
        box.pack_start(turbo, False, False, 0)
        return outer

    def _rules_card(self) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.get_style_context().add_class("cpu-card")
        card.set_border_width(14)
        title = Gtk.Label(label="Adaptive rules", halign=Gtk.Align.START)
        title.get_style_context().add_class("cpu-card-title")
        card.pack_start(title, False, False, 0)
        card.pack_start(
            Gtk.Label(
                label="Used while Adaptive is selected. Power save stays in Silent and Quiet. Adaptive can use all four modes.",
                wrap=True,
                halign=Gtk.Align.START,
            ),
            False,
            False,
            0,
        )
        self.rule_charger = self._rule_combo(self.power_rules["charger"], self._on_rules_changed)
        self.rule_battery = self._rule_combo(self.power_rules["battery"], self._on_rules_changed)
        for title_text, combo in (("On charger", self.rule_charger), ("On battery", self.rule_battery)):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            label = Gtk.Label(label=title_text, halign=Gtk.Align.START)
            label.get_style_context().add_class("cpu-form-label")
            row.pack_start(label, True, True, 0)
            row.pack_start(combo, False, False, 0)
            card.pack_start(row, False, False, 0)
        note = Gtk.Label(
            label="Force silent, Force quiet, Force balance, and Force power stay on that one mode. "
            "Power save moves between Silent and Quiet. Adaptive moves among Silent, Quiet, Balance, and Power.",
            wrap=True,
            halign=Gtk.Align.START,
        )
        note.get_style_context().add_class("cpu-card-desc")
        card.pack_start(note, False, False, 0)
        return card

    def _page_battery(self) -> Gtk.Widget:
        outer, box = self._page_shell(
            "Battery",
            "Charge level, health, charge limit, and the panel battery icon color for each mode.",
        )
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        card.get_style_context().add_class("cpu-card")
        card.set_border_width(14)
        self.battery_icon = BatteryIcon(220, 320)
        card.pack_start(self.battery_icon, False, False, 0)
        facts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        facts.pack_start(self._bind("batt_page", "Battery"), False, False, 0)
        self.ui["batt_page"].get_style_context().add_class("cpu-stat-value")
        for key in ("batt_health_line", "batt_cycles_line", "batt_cap_line", "batt_color_line"):
            facts.pack_start(self._bind(key), False, False, 0)
        card.pack_start(facts, True, True, 0)
        box.pack_start(card, False, False, 0)

        limit = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        limit.get_style_context().add_class("cpu-card")
        limit.set_border_width(14)
        limit.pack_start(Gtk.Label(label="Charge limit", halign=Gtk.Align.START), False, False, 0)
        current = read_charge_limit()
        self.charge_spin = Gtk.SpinButton.new_with_range(50, 100, 1)
        self.charge_spin.get_style_context().add_class("cpu-spin")
        self.charge_spin.set_value(current if current else 80)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.pack_start(Gtk.Label(label="Max charge %"), False, False, 0)
        row.pack_start(self.charge_spin, False, False, 0)
        apply_btn = Gtk.Button(label="Apply limit")
        apply_btn.get_style_context().add_class("cpu-apply-btn")
        apply_btn.connect("clicked", self._on_apply_charge)
        row.pack_start(apply_btn, False, False, 0)
        limit.pack_start(row, False, False, 0)
        presets = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for pct in (60, 70, 80, 90, 100):
            btn = Gtk.Button(label=f"{pct}%" if pct < 100 else "Full")
            btn.get_style_context().add_class("preset-btn")
            btn.connect("clicked", self._on_preset_charge, pct)
            presets.pack_start(btn, False, False, 0)
            self._preset_buttons[pct] = btn
        limit.pack_start(presets, False, False, 0)
        self.limit_label = Gtk.Label(halign=Gtk.Align.START)
        self.limit_label.get_style_context().add_class("cpu-limit-active")
        limit.pack_start(self.limit_label, False, False, 0)
        box.pack_start(limit, False, False, 0)
        box.pack_start(self._battery_icon_card(), False, False, 0)
        return outer

    def _battery_icon_card(self) -> Gtk.Box:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        card.get_style_context().add_class("cpu-card")
        card.set_border_width(14)
        card.pack_start(
            Gtk.Label(label="Battery icon color", halign=Gtk.Align.START),
            False,
            False,
            0,
        )
        card.pack_start(
            Gtk.Label(
                label="Profiles color the battery tank. Turbo colors the percentage only while turbo is on, and lets it go when turbo turns off.",
                wrap=True,
                halign=Gtk.Align.START,
            ),
            False,
            False,
            0,
        )
        settings = load_battery_icon()
        self._icon_color_rows: dict[str, tuple[Gtk.ColorButton, Gtk.Switch]] = {}
        for key, label, default in BATTERY_ICON_MODES:
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            name = Gtk.Label(label=label, halign=Gtk.Align.START, hexpand=True)
            rgba = Gdk.RGBA()
            rgba.parse(settings[key]["color"])
            color = Gtk.ColorButton.new_with_rgba(rgba)
            color.set_use_alpha(False)
            color.set_tooltip_text(f"{label} tank color")
            color.set_sensitive(settings[key]["enabled"])
            color.connect("color-set", self._on_battery_icon_color, key)
            reset = Gtk.Button()
            reset.set_image(Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON))
            reset.set_relief(Gtk.ReliefStyle.NONE)
            reset.set_tooltip_text("Reset color")
            reset.connect("clicked", self._on_battery_icon_reset, key, default)
            switch = Gtk.Switch(valign=Gtk.Align.CENTER)
            switch.set_active(settings[key]["enabled"])
            switch.set_tooltip_text(f"Color the tank in {label}")
            switch.connect("notify::active", self._on_battery_icon_enabled, key)
            row.pack_start(name, True, True, 0)
            row.pack_start(color, False, False, 0)
            row.pack_start(reset, False, False, 0)
            row.pack_start(switch, False, False, 0)
            card.pack_start(row, False, False, 0)
            self._icon_color_rows[key] = (color, switch)
        card.pack_start(self._turbo_percent_row(settings["turbo"]), False, False, 0)
        return card

    def _turbo_percent_row(self, turbo: dict) -> Gtk.Box:
        _key, label, default = TURBO_PERCENT
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        row.set_margin_top(6)
        name = Gtk.Label(label=label, halign=Gtk.Align.START, hexpand=True)
        rgba = Gdk.RGBA()
        rgba.parse(str(turbo.get("color") or default))
        color = Gtk.ColorButton.new_with_rgba(rgba)
        color.set_use_alpha(False)
        color.set_tooltip_text("Percentage color while turbo is on")
        color.set_sensitive(bool(turbo.get("enabled", True)))
        color.connect("color-set", self._on_turbo_percent_color)
        reset = Gtk.Button()
        reset.set_image(Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON))
        reset.set_relief(Gtk.ReliefStyle.NONE)
        reset.set_tooltip_text("Reset color")
        reset.connect("clicked", self._on_turbo_percent_reset)
        switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        switch.set_active(bool(turbo.get("enabled", True)))
        switch.set_tooltip_text("Color the percentage while turbo is on")
        switch.connect("notify::active", self._on_turbo_percent_enabled)
        row.pack_start(name, True, True, 0)
        row.pack_start(color, False, False, 0)
        row.pack_start(reset, False, False, 0)
        row.pack_start(switch, False, False, 0)
        self._turbo_percent_color = color
        self._turbo_percent_switch = switch
        return row

    def _on_battery_icon_color(self, _button: Gtk.ColorButton, key: str) -> None:
        if self._updating:
            return
        self._save_battery_icon_rows()

    def _on_battery_icon_enabled(self, switch: Gtk.Switch, _pspec, key: str) -> None:
        if self._updating or key not in self._icon_color_rows:
            return
        color, _switch = self._icon_color_rows[key]
        color.set_sensitive(switch.get_active())
        self._save_battery_icon_rows()

    def _on_battery_icon_reset(self, _button: Gtk.Button, key: str, default: str) -> None:
        color, _switch = self._icon_color_rows[key]
        rgba = Gdk.RGBA()
        rgba.parse(default)
        self._updating = True
        try:
            color.set_rgba(rgba)
        finally:
            self._updating = False
        self._save_battery_icon_rows()

    def _on_turbo_percent_color(self, _button: Gtk.ColorButton) -> None:
        if self._updating:
            return
        self._save_battery_icon_rows()

    def _on_turbo_percent_enabled(self, switch: Gtk.Switch, _pspec) -> None:
        if self._updating or not hasattr(self, "_turbo_percent_color"):
            return
        self._turbo_percent_color.set_sensitive(switch.get_active())
        self._save_battery_icon_rows()

    def _on_turbo_percent_reset(self, _button: Gtk.Button) -> None:
        rgba = Gdk.RGBA()
        rgba.parse(TURBO_PERCENT[2])
        self._updating = True
        try:
            self._turbo_percent_color.set_rgba(rgba)
        finally:
            self._updating = False
        self._save_battery_icon_rows()

    def _save_battery_icon_rows(self) -> None:
        settings = load_battery_icon()
        for key, (color, switch) in self._icon_color_rows.items():
            settings[key]["color"] = rgba_to_hex(color.get_rgba())
            settings[key]["enabled"] = switch.get_active()
        if hasattr(self, "_turbo_percent_color"):
            settings["turbo"] = {
                "enabled": self._turbo_percent_switch.get_active(),
                "color": rgba_to_hex(self._turbo_percent_color.get_rgba()),
            }
        save_battery_icon(settings)

    def _page_display(self) -> Gtk.Widget:
        cfg = load_smooth_brightness_config()
        outer, box = self._page_shell(
            "Display",
            "Experimental smooth auto brightness (separate login service). Behavior may change.",
        )
        auto_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        section_head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        section_title = Gtk.Label(label="Auto brightness service", halign=Gtk.Align.START)
        section_title.get_style_context().add_class("cpu-card-title")
        beta_badge = Gtk.Label(label="BETA")
        beta_badge.get_style_context().add_class("cpu-beta-badge")
        beta_badge.set_valign(Gtk.Align.CENTER)
        section_head.pack_start(section_title, False, False, 0)
        section_head.pack_start(beta_badge, False, False, 0)
        auto_section.pack_start(section_head, False, False, 0)

        status_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        status_card.get_style_context().add_class("cpu-card")
        status_card.set_border_width(14)
        self.display_status = Gtk.Label(halign=Gtk.Align.START)
        self.display_status.get_style_context().add_class("cpu-card-desc")
        self.display_status.set_line_wrap(True)
        status_card.pack_start(self.display_status, False, False, 0)
        amb_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        amb_lbl = Gtk.Label(label="Automatic brightness", halign=Gtk.Align.START)
        amb_lbl.get_style_context().add_class("cpu-form-label")
        self.display_ambient = Gtk.Switch()
        self.display_ambient.set_valign(Gtk.Align.CENTER)
        self.display_ambient.set_active(read_ambient_brightness_enabled())
        self.display_ambient.connect("notify::active", self._on_display_ambient)
        amb_row.pack_start(amb_lbl, True, True, 0)
        amb_row.pack_start(self.display_ambient, False, False, 0)
        status_card.pack_start(amb_row, False, False, 0)
        status_card.pack_start(
            Gtk.Label(
                label="Uses the same toggle as Zorin Settings. The smooth brightness service must be running.",
                wrap=True,
                halign=Gtk.Align.START,
            ),
            False,
            False,
            0,
        )
        self.display_install_btn = Gtk.Button(label="Install smooth brightness service")
        self.display_install_btn.get_style_context().add_class("cpu-apply-btn")
        self.display_install_btn.set_halign(Gtk.Align.START)
        self.display_install_btn.connect("clicked", self._on_install_display_smooth)
        status_card.pack_start(self.display_install_btn, False, False, 0)

        fade_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        fade_card.get_style_context().add_class("cpu-card")
        fade_card.set_border_width(14)
        fade_card.pack_start(Gtk.Label(label="Screen fade duration", halign=Gtk.Align.START), False, False, 0)
        fade_card.pack_start(
            Gtk.Label(
                label="How long the display takes to reach a new brightness level. Higher feels calmer.",
                wrap=True,
                halign=Gtk.Align.START,
            ),
            False,
            False,
            0,
        )
        fade_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.display_fade = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1.0, 20.0, 0.5)
        self.display_fade.set_value(cfg["brightness_time_constant_sec"])
        self.display_fade.set_digits(1)
        self.display_fade.set_hexpand(True)
        self.display_fade.get_style_context().add_class("cpu-combo")
        self.display_fade.connect("value-changed", self._on_display_fade)
        self.display_fade_val = Gtk.Label(label=f"{cfg['brightness_time_constant_sec']:.1f} s")
        fade_row.pack_start(self.display_fade, True, True, 0)
        fade_row.pack_start(self.display_fade_val, False, False, 0)
        fade_card.pack_start(fade_row, False, False, 0)

        amb_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        amb_card.get_style_context().add_class("cpu-card")
        amb_card.set_border_width(14)
        amb_card.pack_start(Gtk.Label(label="Room light response", halign=Gtk.Align.START), False, False, 0)
        amb_card.pack_start(
            Gtk.Label(
                label="How quickly auto brightness reacts to the ambient sensor. Lower values change more gently.",
                wrap=True,
                halign=Gtk.Align.START,
            ),
            False,
            False,
            0,
        )
        amb_srow = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.display_ambient_smooth = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.01, 0.12, 0.005)
        self.display_ambient_smooth.set_value(cfg["ambient_bandwidth_hz"])
        self.display_ambient_smooth.set_digits(3)
        self.display_ambient_smooth.set_hexpand(True)
        self.display_ambient_smooth.get_style_context().add_class("cpu-combo")
        self.display_ambient_smooth.connect("value-changed", self._on_display_ambient_smooth)
        self.display_ambient_val = Gtk.Label(label=f"{cfg['ambient_bandwidth_hz']:.3f} Hz")
        amb_srow.pack_start(self.display_ambient_smooth, True, True, 0)
        amb_srow.pack_start(self.display_ambient_val, False, False, 0)
        amb_card.pack_start(amb_srow, False, False, 0)
        self.display_apply_btn = Gtk.Button(label="Apply display smoothing")
        self.display_apply_btn.get_style_context().add_class("cpu-apply-btn")
        self.display_apply_btn.set_halign(Gtk.Align.START)
        self.display_apply_btn.connect("clicked", self._on_apply_display_smooth)
        amb_card.pack_start(self.display_apply_btn, False, False, 0)
        auto_section.pack_start(status_card, False, False, 0)
        auto_section.pack_start(fade_card, False, False, 0)
        auto_section.pack_start(amb_card, False, False, 0)
        box.pack_start(auto_section, False, False, 0)
        self._sync_display_ui()
        return outer

    def _page_keyboard(self) -> Gtk.Widget:
        kbd = get_keyboard()
        outer, box = self._page_shell(
            "Keyboard",
            "Keyboard backlight brightness, and how long it stays on after you stop using the computer.",
        )
        bright = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        bright.get_style_context().add_class("cpu-card")
        bright.set_border_width(14)
        bright.pack_start(Gtk.Label(label="Brightness", halign=Gtk.Align.START), False, False, 0)
        bright.pack_start(
            Gtk.Label(
                label="Off, low, medium, or high. Matches the keyboard backlight key on your Galaxy Book.",
                wrap=True,
                halign=Gtk.Align.START,
            ),
            False,
            False,
            0,
        )
        maximum = max(1, kbd.max_brightness)
        self.kbd_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, maximum, 1)
        self.kbd_scale.set_digits(0)
        self.kbd_scale.set_hexpand(True)
        self.kbd_scale.set_value(kbd.level if kbd.available else 0)
        self.kbd_scale.get_style_context().add_class("cpu-combo")
        names = {0: "Off", 1: "Low", 2: "Medium", 3: "High"}
        for step in range(maximum + 1):
            self.kbd_scale.add_mark(
                step,
                Gtk.PositionType.BOTTOM,
                names.get(step, str(step)),
            )
        self.kbd_scale.connect("value-changed", self._on_kbd_brightness)
        self.kbd_scale.set_sensitive(kbd.available)
        bright.pack_start(self.kbd_scale, False, False, 0)
        box.pack_start(bright, False, False, 0)

        timer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        timer.get_style_context().add_class("cpu-card")
        timer.set_border_width(14)
        timer.pack_start(Gtk.Label(label="Turn off after", halign=Gtk.Align.START), False, False, 0)
        timer.pack_start(
            Gtk.Label(
                label="The light turns off after this long with no keyboard or mouse use, then comes back when you use the computer.",
                wrap=True,
                halign=Gtk.Align.START,
            ),
            False,
            False,
            0,
        )
        presets = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._kbd_timeout_buttons: dict[int, Gtk.Button] = {}
        for seconds, label in KBD_TIMEOUTS:
            btn = Gtk.Button(label=label)
            btn.get_style_context().add_class("preset-btn")
            btn.set_sensitive(kbd.available)
            btn.connect("clicked", self._on_kbd_timeout, seconds)
            presets.pack_start(btn, False, False, 0)
            self._kbd_timeout_buttons[seconds] = btn
        timer.pack_start(presets, False, False, 0)
        self.kbd_status = Gtk.Label(halign=Gtk.Align.START)
        self.kbd_status.set_line_wrap(True)
        self.kbd_status.get_style_context().add_class("cpu-limit-active")
        timer.pack_start(self.kbd_status, False, False, 0)
        box.pack_start(timer, False, False, 0)
        kbd.add_listener(self._on_kbd_external)
        self.connect("destroy", self._on_kbd_window_destroy)
        self._sync_kbd_ui()
        return outer

    def _on_kbd_window_destroy(self, _widget) -> None:
        get_keyboard().remove_listener(self._on_kbd_external)

    def _on_kbd_brightness(self, scale: Gtk.Scale) -> None:
        if self._updating:
            return
        get_keyboard().set_level(int(round(scale.get_value())))

    def _on_kbd_timeout(self, _btn: Gtk.Button, seconds: int) -> None:
        get_keyboard().set_timeout(seconds)

    def _on_kbd_external(self) -> None:
        self._sync_kbd_ui()

    def _sync_kbd_ui(self) -> None:
        if not hasattr(self, "kbd_scale"):
            return
        kbd = get_keyboard()
        self._updating = True
        try:
            if int(round(self.kbd_scale.get_value())) != kbd.level:
                self.kbd_scale.set_value(kbd.level)
            for seconds, btn in self._kbd_timeout_buttons.items():
                ctx = btn.get_style_context()
                if seconds == kbd.timeout_sec:
                    ctx.add_class("selected")
                else:
                    ctx.remove_class("selected")
            self.kbd_status.set_text(kbd.status_line())
        finally:
            self._updating = False

    def _page_memory(self) -> Gtk.Widget:
        outer, box = self._page_shell(
            "Memory",
            "RAM and swap overview, swappiness, and zram (via zram-tools on Zorin/Ubuntu).",
        )
        overview = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        overview.get_style_context().add_class("cpu-card")
        overview.set_border_width(14)
        overview.pack_start(Gtk.Label(label="Overview", halign=Gtk.Align.START), False, False, 0)
        for key in ("mem_ram_line", "mem_swap_line", "mem_zram_line", "mem_swappiness_line"):
            lbl = self._bind(key)
            lbl.get_style_context().add_class("cpu-card-desc")
            overview.pack_start(lbl, False, False, 0)
        box.pack_start(overview, False, False, 0)

        swap_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        swap_card.get_style_context().add_class("cpu-card")
        swap_card.set_border_width(14)
        swap_card.pack_start(
            Gtk.Label(label="Swap devices", halign=Gtk.Align.START),
            False,
            False,
            0,
        )
        swap_card.pack_start(
            Gtk.Label(
                label="Shows all active swap (zram, swap file, partitions). Removing swap is not done here.",
                wrap=True,
                halign=Gtk.Align.START,
            ),
            False,
            False,
            0,
        )
        self.mem_swap_list = Gtk.Label(label="—")
        self.mem_swap_list.set_halign(Gtk.Align.START)
        self.mem_swap_list.set_line_wrap(True)
        self.mem_swap_list.get_style_context().add_class("cpu-card-desc")
        swap_card.pack_start(self.mem_swap_list, False, False, 0)
        box.pack_start(swap_card, False, False, 0)

        swappiness_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        swappiness_card.get_style_context().add_class("cpu-card")
        swappiness_card.set_border_width(14)
        swappiness_card.pack_start(Gtk.Label(label="Swappiness", halign=Gtk.Align.START), False, False, 0)
        swappiness_card.pack_start(
            Gtk.Label(
                label="How eagerly the kernel moves memory to swap. Lower keeps more in RAM; higher frees RAM sooner.",
                wrap=True,
                halign=Gtk.Align.START,
            ),
            False,
            False,
            0,
        )
        sw_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.mem_swappiness = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 200, 1)
        self.mem_swappiness.set_value(read_swappiness())
        self.mem_swappiness.set_digits(0)
        self.mem_swappiness.set_hexpand(True)
        self.mem_swappiness.get_style_context().add_class("cpu-combo")
        self.mem_swappiness_val = Gtk.Label(label=str(read_swappiness()))
        self.mem_swappiness.connect("value-changed", self._on_swappiness_scale)
        sw_row.pack_start(self.mem_swappiness, True, True, 0)
        sw_row.pack_start(self.mem_swappiness_val, False, False, 0)
        sw_apply = Gtk.Button(label="Apply swappiness")
        sw_apply.get_style_context().add_class("cpu-apply-btn")
        sw_apply.connect("clicked", self._on_apply_swappiness)
        swappiness_card.pack_start(sw_row, False, False, 0)
        swappiness_card.pack_start(sw_apply, False, False, 0)
        box.pack_start(swappiness_card, False, False, 0)

        zconf = read_zramswap_conf()
        zram_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        zram_card.get_style_context().add_class("cpu-card")
        zram_card.set_border_width(14)
        zram_card.pack_start(Gtk.Label(label="Zram (compressed RAM swap)", halign=Gtk.Align.START), False, False, 0)
        if zconf["available"]:
            zram_card.pack_start(
                Gtk.Label(
                    label="Uses the zram-tools package (zramswap service). Changes need your password.",
                    wrap=True,
                    halign=Gtk.Align.START,
                ),
                False,
                False,
                0,
            )
            self.mem_zram_boot = Gtk.Switch()
            self.mem_zram_boot.set_active(is_zramswap_boot_enabled())
            self.mem_zram_boot.connect("notify::active", self._on_zram_boot)
            zram_card.pack_start(
                self._switch_row("Enable zram at boot", "Starts compressed swap via zramswap.service", self.mem_zram_boot),
                False,
                False,
                0,
            )
            algos, active_algo = read_zram_comp_algorithms()
            zgrid = Gtk.Grid(column_spacing=16, row_spacing=10)
            pct_l = Gtk.Label(label="RAM for zram (%)")
            pct_l.get_style_context().add_class("cpu-form-label")
            self.mem_zram_percent = Gtk.SpinButton.new_with_range(5, 100, 1)
            self.mem_zram_percent.set_value(int(zconf["percent"]))
            self.mem_zram_percent.get_style_context().add_class("cpu-spin")
            algo_l = Gtk.Label(label="Compression")
            algo_l.get_style_context().add_class("cpu-form-label")
            self.mem_zram_algo = Gtk.ComboBoxText()
            for algo in algos:
                self.mem_zram_algo.append_text(algo)
            if active_algo in algos:
                self.mem_zram_algo.set_active(algos.index(active_algo))
            elif algos:
                self.mem_zram_algo.set_active(0)
            prio_l = Gtk.Label(label="Swap priority")
            prio_l.get_style_context().add_class("cpu-form-label")
            self.mem_zram_prio = Gtk.SpinButton.new_with_range(-1, 32767, 1)
            self.mem_zram_prio.set_value(int(zconf["priority"]))
            self.mem_zram_prio.get_style_context().add_class("cpu-spin")
            zgrid.attach(pct_l, 0, 0, 1, 1)
            zgrid.attach(self.mem_zram_percent, 1, 0, 1, 1)
            zgrid.attach(algo_l, 0, 1, 1, 1)
            zgrid.attach(self.mem_zram_algo, 1, 1, 1, 1)
            zgrid.attach(prio_l, 0, 2, 1, 1)
            zgrid.attach(self.mem_zram_prio, 1, 2, 1, 1)
            zram_card.pack_start(zgrid, False, False, 0)
            z_apply = Gtk.Button(label="Apply zram settings")
            z_apply.get_style_context().add_class("cpu-apply-btn")
            z_apply.connect("clicked", self._on_apply_zram)
            zram_card.pack_start(z_apply, False, False, 0)
        else:
            zram_card.pack_start(
                Gtk.Label(
                    label="zram-tools not detected. Install with: sudo apt install zram-tools",
                    wrap=True,
                    halign=Gtk.Align.START,
                ),
                False,
                False,
                0,
            )
        box.pack_start(zram_card, False, False, 0)
        return outer

    def _on_swappiness_scale(self, scale: Gtk.Scale) -> None:
        self.mem_swappiness_val.set_text(str(int(scale.get_value())))

    def _on_apply_swappiness(self, _btn: Gtk.Button) -> None:
        if self._updating:
            return
        value = int(self.mem_swappiness.get_value())

        def work():
            return run_set_swappiness(value)

        def done(ok: bool, msg: str) -> None:
            self._set_status(msg)
            if ok:
                self._paint_memory()

        if not schedule_apply(work, done):
            self._set_status("A password dialog is already open. Finish that first.")
            return
        self._set_status("Waiting for the password dialog…")

    def _on_zram_boot(self, switch: Gtk.Switch, _pspec) -> None:
        if self._updating:
            return
        enable = switch.get_active()
        previous = not enable

        def work():
            return run_zramswap_boot(enable)

        def done(ok: bool, msg: str) -> None:
            self._set_status(msg)
            if ok:
                self._paint_memory()
            else:
                self._updating = True
                switch.set_active(previous)
                self._updating = False

        if not schedule_apply(work, done):
            self._updating = True
            switch.set_active(previous)
            self._updating = False
            self._set_status("A password dialog is already open. Finish that first.")
            return
        self._set_status("Waiting for the password dialog…")

    def _on_apply_zram(self, _btn: Gtk.Button) -> None:
        if self._updating or not zram_tools_available():
            return
        algo = self.mem_zram_algo.get_active_text() or "lz4"
        percent = int(self.mem_zram_percent.get_value())
        priority = int(self.mem_zram_prio.get_value())

        def work():
            return run_apply_zramswap(algo, percent, priority)

        def done(ok: bool, msg: str) -> None:
            self._set_status(msg)
            if ok:
                self._paint_memory()

        if not schedule_apply(work, done):
            self._set_status("A password dialog is already open. Finish that first.")
            return
        self._set_status("Waiting for the password dialog…")

    def _paint_memory(self) -> None:
        if "mem_ram_line" not in self.ui:
            return
        overview = read_memory_overview()
        self.ui["mem_ram_line"].set_text(overview["ram"])
        self.ui["mem_swap_line"].set_text(overview["swap"])
        self.ui["mem_zram_line"].set_text(overview["zram"])
        self.ui["mem_swappiness_line"].set_text(overview["swappiness"])
        if hasattr(self, "mem_swap_list"):
            self.mem_swap_list.set_text(overview["swap_list"])
        if hasattr(self, "mem_swappiness"):
            self._updating = True
            self.mem_swappiness.set_value(read_swappiness())
            self.mem_swappiness_val.set_text(str(read_swappiness()))
            self._updating = False
        if hasattr(self, "mem_zram_boot"):
            self._updating = True
            self.mem_zram_boot.set_active(is_zramswap_boot_enabled())
            self._updating = False

    def _rule_combo(self, active: str, on_change) -> Gtk.ComboBoxText:
        combo = Gtk.ComboBoxText()
        combo.get_style_context().add_class("cpu-combo")
        for key, label in POWER_RULE_LABELS.items():
            combo.append(key, label)
        combo.set_active_id(normalize_power_rule(active))
        combo.connect("changed", on_change)
        return combo

    def _on_rules_changed(self, _widget) -> None:
        if self._updating or not hasattr(self, "rule_battery"):
            return
        self.power_rules = {
            "charger": normalize_power_rule(self.rule_charger.get_active_id(), "adaptive"),
            "battery": normalize_power_rule(self.rule_battery.get_active_id(), "powersave"),
        }
        save_power_rules(self.power_rules)
        self._plat_ts = 0.0
        self._adaptive_armed = False
        self._plat_last = read_platform_profile()
        self._set_status("Rules saved. Adaptive will follow them.")

    def _page_scheduler(self) -> Gtk.Widget:
        outer, box = self._page_shell(
            "Scheduler",
            "When a switch is on, that rule replaces the Adaptive rule in CPU for that situation. Same choices: Adaptive, Power save, or a forced Samsung mode.",
        )
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.get_style_context().add_class("cpu-card")
        card.set_border_width(14)
        self.sch_charger = Gtk.Switch()
        self.sch_battery = Gtk.Switch()
        self.sch_low = Gtk.Switch()
        self.sch_charger.set_active(bool(self.schedule["charger_on"]))
        self.sch_battery.set_active(bool(self.schedule["battery_on"]))
        self.sch_low.set_active(bool(self.schedule["low_on"]))
        self.sch_charger_rule = self._rule_combo(self.schedule["charger_rule"], self._save_scheduler)
        self.sch_battery_rule = self._rule_combo(self.schedule["battery_rule"], self._save_scheduler)
        self.sch_low_rule = self._rule_combo(self.schedule["low_rule"], self._save_scheduler)
        for sw in (self.sch_charger, self.sch_battery, self.sch_low):
            sw.connect("notify::active", self._save_scheduler)
        card.pack_start(self._schedule_row("On charger", self.sch_charger, self.sch_charger_rule), False, False, 0)
        card.pack_start(self._schedule_row("On battery", self.sch_battery, self.sch_battery_rule), False, False, 0)
        card.pack_start(self._schedule_row("Low battery (under 20%)", self.sch_low, self.sch_low_rule), False, False, 0)
        box.pack_start(card, False, False, 0)
        return outer

    def _schedule_row(self, title: str, switch: Gtk.Switch, combo: Gtk.ComboBoxText) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        label = Gtk.Label(label=title, halign=Gtk.Align.START)
        row.pack_start(label, True, True, 0)
        row.pack_start(combo, False, False, 0)
        row.pack_start(switch, False, False, 0)
        return row

    def _switch_row(self, title: str, subtitle: str, switch: Gtk.Switch) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        t = Gtk.Label(label=title, halign=Gtk.Align.START)
        s = Gtk.Label(label=subtitle, halign=Gtk.Align.START)
        s.get_style_context().add_class("cpu-card-desc")
        text.pack_start(t, False, False, 0)
        text.pack_start(s, False, False, 0)
        row.pack_start(text, True, True, 0)
        row.pack_start(switch, False, False, 0)
        return row

    def _page_monitor(self) -> Gtk.Widget:
        outer, box = self._page_shell("System monitor", "CPU, memory, disk, and network.")
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("cpu-card")
        card.set_border_width(14)
        card.pack_start(self._bind("mon_cpu", "CPU"), False, False, 0)
        card.pack_start(self.mem_chart, False, False, 0)
        card.pack_start(self._bind("mon_mem", "Memory"), False, False, 0)
        card.pack_start(self._bind("mon_disk", "Disk"), False, False, 0)
        card.pack_start(self._bind("mon_net", "Network"), False, False, 0)
        box.pack_start(card, False, False, 0)
        return outer

    def _page_settings(self) -> Gtk.Widget:
        outer, box = self._page_shell("Settings", "Daemon and how this app applies changes.")
        password_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        password_card.get_style_context().add_class("cpu-card")
        password_card.set_border_width(14)
        password_card.pack_start(
            Gtk.Label(label="Login password", halign=Gtk.Align.START),
            False,
            False,
            0,
        )
        password_card.pack_start(
            Gtk.Label(
                label="Enter your login password once. Every CPU, fan, turbo, charge, and memory change then applies by itself. If you change that password later, enter the new one here.",
                wrap=True,
                halign=Gtk.Align.START,
            ),
            False,
            False,
            0,
        )
        self.admin_pass = Gtk.Entry()
        self.admin_pass.set_visibility(False)
        self.admin_pass.set_placeholder_text("Login password")
        self.admin_pass.set_hexpand(True)
        self.admin_pass.connect("activate", self._on_save_admin_password)
        self.admin_pass_status = Gtk.Label(halign=Gtk.Align.START)
        self.admin_pass_status.get_style_context().add_class("cpu-limit-active")
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        save_pw = Gtk.Button(label="Save password")
        save_pw.get_style_context().add_class("cpu-apply-btn")
        save_pw.connect("clicked", self._on_save_admin_password)
        forget_pw = Gtk.Button(label="Forget")
        forget_pw.connect("clicked", self._on_forget_admin_password)
        row.pack_start(self.admin_pass, True, True, 0)
        row.pack_start(save_pw, False, False, 0)
        row.pack_start(forget_pw, False, False, 0)
        password_card.pack_start(row, False, False, 0)
        password_card.pack_start(self.admin_pass_status, False, False, 0)
        box.pack_start(password_card, False, False, 0)
        self._refresh_admin_password_status()

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("cpu-card")
        card.set_border_width(14)
        note = Gtk.Label(
            label="If no password is saved, applying a change asks once in the system dialog. "
            "If profiles say the daemon is not running, repair it below.",
            wrap=True,
            halign=Gtk.Align.START,
        )
        note.get_style_context().add_class("cpu-card-desc")
        repair = Gtk.Button(label="Repair and start daemon")
        repair.get_style_context().add_class("cpu-apply-btn")
        repair.set_halign(Gtk.Align.START)
        repair.connect("clicked", self._on_install_daemon)
        card.pack_start(note, False, False, 0)
        card.pack_start(repair, False, False, 0)
        card.pack_start(self._bind("settings_host", ""), False, False, 0)
        box.pack_start(card, False, False, 0)
        return outer

    def _refresh_admin_password_status(self) -> None:
        if not hasattr(self, "admin_pass_status"):
            return
        if admin_password_saved():
            self.admin_pass_status.set_text("Password saved. Changes apply without asking.")
            self.admin_pass.set_placeholder_text("Enter a new password to replace it")
        else:
            self.admin_pass_status.set_text("No password saved. Changes will ask.")
            self.admin_pass.set_placeholder_text("Login password")

    def _on_save_admin_password(self, *_args) -> None:
        password = self.admin_pass.get_text()
        self.admin_pass.set_text("")
        if not password:
            self._set_status("Enter your login password.")
            return

        def done(ok: bool, msg: str) -> None:
            self._set_status(msg)
            self._refresh_admin_password_status()

        if not schedule_apply(lambda: save_admin_password(password), done):
            self._set_status("A password dialog is already open. Finish that first.")

    def _on_forget_admin_password(self, _btn: Gtk.Button) -> None:
        def done(ok: bool, msg: str) -> None:
            self._set_status(msg)
            self._refresh_admin_password_status()

        if not schedule_apply(forget_admin_password, done):
            self._set_status("A password dialog is already open. Finish that first.")

    def _quick_profile(self, mode: str) -> None:
        previous = self.profile_tiles.current
        if mode == previous:
            return
        self.profile_tiles.set_current(mode)
        self._on_profile_pick(mode, previous)

    def _quick_turbo(self, mode: str) -> None:
        previous = self.turbo_choice.current
        if mode == previous:
            return
        self.turbo_choice.set_current(mode)
        self._on_turbo_pick(mode, previous)

    def _save_scheduler(self, *_args) -> None:
        if self._updating or not hasattr(self, "sch_low_rule"):
            return
        self.schedule = {
            "charger_on": self.sch_charger.get_active(),
            "charger_rule": normalize_power_rule(self.sch_charger_rule.get_active_id(), "adaptive"),
            "battery_on": self.sch_battery.get_active(),
            "battery_rule": normalize_power_rule(self.sch_battery_rule.get_active_id(), "powersave"),
            "low_on": self.sch_low.get_active(),
            "low_rule": normalize_power_rule(self.sch_low_rule.get_active_id(), "silent"),
            "low_pct": 20,
        }
        save_schedule(self.schedule)
        self._plat_ts = 0.0

    def _tick(self) -> bool:
        self.live = read_live()
        self._maybe_adaptive()
        if not self.get_visible():
            return True
        try:
            import psutil

            counters = psutil.net_io_counters()
            now = time.time()
            if self._net_prev:
                prev_b, prev_t = self._net_prev
                delta = (counters.bytes_sent + counters.bytes_recv) - prev_b
                dt = max(0.2, now - prev_t)
                self.live["net_kbps"] = (delta / 1024) / dt
            self._net_prev = (counters.bytes_sent + counters.bytes_recv, now)
        except Exception:
            pass
        cpu = self.live["cpu_pct"]
        temp = self.live["temp"] or 0
        watts = self.live["rate_w"] or 0
        self.hist_cpu.append(cpu)
        self.hist_temp.append(min(100.0, temp))
        self.hist_watt.append(min(100.0, watts * 2))
        self.hist_mem.append(self.live["mem_pct"])
        self.gauge.set_reading(cpu, f"{cpu:.0f}%", "CPU")
        self.chart.queue_draw()
        self.mem_chart.queue_draw()
        self._paint_live()
        self._maybe_schedule()
        return True

    def _paint_live(self) -> None:
        live = self.live
        mode = read_samsung_mode()
        turbo = read_turbo_override()
        turbo_live = read_turbo_live()
        prof_txt = format_profile_status()
        turbo_txt = format_turbo_status(turbo, turbo_live)
        limit = read_charge_limit()
        self.ui["dash_profile"].set_text(prof_txt)
        self.ui["dash_turbo"].set_text(turbo_txt)
        if turbo == "auto":
            self.turbo_choice.reflect_live_turbo(turbo_live)
        self.status_profile.set_markup(f"Profile: <b>{prof_txt}</b>")
        self.status_turbo.set_markup(f"Turbo: <b>{turbo_txt}</b>")
        self.ui["dash_limit"].set_text(f"{limit}%" if limit is not None else "—")
        state = live["state"].replace("-", " ").title()
        self.ui["dash_state"].set_text(state)
        self.ui["cpu_title"].set_text(live["cpu_name"])
        self.ui["cpu_sub"].set_text(live["cores"])
        self.ui["freq"].set_text(f"Frequency  {live['freq_ghz']:.2f} GHz")
        temp = f"{live['temp']:.0f} °C" if live["temp"] is not None else "—"
        self.ui["temp"].set_text(f"Temperature  {temp}")
        rate = f"{live['rate_w']:.1f} W" if live["rate_w"] is not None else "—"
        self.ui["watts"].set_text(f"System power  {rate}")
        mv = read_memory_visual_stats()
        if hasattr(self, "dash_memory"):
            self.dash_memory.set_reading(
                float(mv["ram_pct"]),
                float(mv["ram_used"]),
                float(mv["ram_free"]),
                float(mv["ram_total"]),
                float(mv["swap_pct"]),
                float(mv["swap_used"]),
                float(mv["swap_total"]),
                float(mv["zram_pct"]),
                float(mv["zram_used"]),
                float(mv["zram_total"]),
                bool(mv["zram_active"]),
            )
        pct = f"{live['percent']:.0f}%" if live["percent"] is not None else "—"
        self.ui["batt_big"].set_text(pct)
        charging = live["state"] in ("charging", "pending-charge")
        powersave = mode in ("silent", "quiet")
        fill = battery_profile_color()
        self.dash_battery.set_reading(live["percent"], charging, powersave, fill)
        self.battery_icon.set_reading(live["percent"], charging, powersave, fill)
        running = SAMSUNG_LABELS.get(running_battery_mode(), running_battery_mode())
        if live["percent"] is not None and live["percent"] < 20:
            color_note = "Low battery — shown in red"
        elif fill is not None and mode == "adaptive":
            color_note = f"Adaptive — showing {running}"
        elif fill is not None:
            color_note = f"{running} color"
        elif powersave:
            color_note = "Power saver — shown in yellow"
        elif charging:
            color_note = "Charging"
        else:
            color_note = "Charge level — shown in green"
        self.ui["batt_color_line"].set_text(color_note)
        self.ui["batt_page"].set_text(f"{pct}   {state}")
        health = live["health"] or "—"
        self.ui["health"].set_text(f"Health  {health}")
        self.ui["cycles"].set_text(f"Cycles  {live['cycles'] or '—'}")
        self.ui["design"].set_text(f"Design capacity  {live['design_wh'] or '—'}")
        self.ui["full"].set_text(f"Full capacity  {live['full_wh'] or '—'}")
        self.ui["time_left"].set_text(f"Time remaining  {live['time_left']}")
        self.ui["rate"].set_text(f"Rate  {rate}")
        self.ui["batt_health_line"].set_text(f"Health {health}   ·   wear from design capacity")
        self.ui["batt_cycles_line"].set_text(f"Charge cycles  {live['cycles'] or '—'}")
        self.ui["batt_cap_line"].set_text(
            f"Full {live['full_wh'] or '—'}   ·   design {live['design_wh'] or '—'}"
        )
        self.ui["cpu_page_line"].set_text(
            f"{live['cpu_name']}   ·   {live['freq_ghz']:.2f} GHz   ·   {temp}   ·   {live['cpu_pct']:.0f}% used"
        )
        self.ui["plat_now"].set_text(
            "Firmware mode now: " + {
                "low-power": "Silent",
                "quiet": "Quiet",
                "balanced": "Balance",
                "performance": "Power",
            }.get(read_platform_profile(), read_platform_profile())
        )
        conf = read_auto_cpufreq_conf()
        self.ui["sum_gov_ac"].set_text("On charger  " + POWER_RULE_LABELS.get(self.power_rules["charger"], ""))
        self.ui["sum_gov_bat"].set_text("On battery  " + POWER_RULE_LABELS.get(self.power_rules["battery"], ""))
        self.ui["sum_turbo_ac"].set_text("Turbo (charger)  " + TURBO_LABELS.get(conf["charger"]["turbo"], conf["charger"]["turbo"]))
        self.ui["sum_turbo_bat"].set_text("Turbo (battery)  " + TURBO_LABELS.get(conf["battery"]["turbo"], conf["battery"]["turbo"]))
        self.ui["mon_cpu"].set_text(f"CPU  {live['cpu_pct']:.0f}%   ·   {live['freq_ghz']:.2f} GHz   ·   {temp}")
        self.ui["mon_mem"].set_text(f"Memory  {live['mem_pct']:.0f}% used")
        self.ui["mon_disk"].set_text(f"Disk  {live['disk_pct']:.0f}% of /")
        self.ui["mon_net"].set_text(f"Network  {live['net_kbps']:.0f} KB/s")
        self._paint_memory()
        self.ui["sidebar_foot"].set_text(f"{live['product']}\n{live['cpu_name']}")
        self.ui["settings_host"].set_text(f"{live['product']}  ·  {live['cpu_name']}")
        for mode_name, btn in (
            ("adaptive", self.ui.get("quick_adaptive")),
            ("silent", self.ui.get("quick_silent")),
            ("quiet", self.ui.get("quick_quiet")),
            ("balance", self.ui.get("quick_balance")),
            ("power", self.ui.get("quick_power")),
        ):
            if not isinstance(btn, Gtk.Button):
                continue
            ctx = btn.get_style_context()
            if mode_name == mode:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")

    def _scheduled_rule(self) -> str | None:
        sch = self.schedule
        plugged = bool(self.live.get("plugged"))
        pct = self.live.get("percent")
        if sch.get("low_on") and pct is not None and pct < int(sch.get("low_pct", 20)) and not plugged:
            return normalize_power_rule(sch.get("low_rule"), "silent")
        if sch.get("charger_on") and plugged:
            return normalize_power_rule(sch.get("charger_rule"), "adaptive")
        if sch.get("battery_on") and not plugged:
            return normalize_power_rule(sch.get("battery_rule"), "powersave")
        return None

    def _maybe_schedule(self) -> None:
        return

    def _maybe_adaptive(self) -> None:
        if _APPLY_LOCK.locked() or self._updating:
            return
        scheduled = self._scheduled_rule()
        manual = read_samsung_mode()
        if scheduled:
            self._under_schedule = True
            self._follow_rule(scheduled, "Scheduler")
            return
        if self._under_schedule:
            self._under_schedule = False
            self._plat_ts = 0.0
            if manual != "adaptive":
                previous = self.profile_tiles.current
                self._apply_choice(lambda: run_profile(manual), previous, self.profile_tiles)
                return
        if manual != "adaptive":
            return
        rule = self.power_rules["charger" if self.live.get("plugged") else "battery"]
        self._follow_rule(rule, "Rules")

    def _follow_rule(self, rule: str, source: str) -> None:
        now = time.time()
        if now - self._plat_ts < 20:
            return
        load = float(self.live.get("cpu_pct") or 0)
        temp = self.live.get("temp")
        live = read_platform_profile()
        choice = platform_for_rule(
            rule,
            load,
            temp,
            bool(self.live.get("plugged")),
            self._plat_last,
        )
        cooling = cooling_hold(choice, load, temp, self._plat_last)
        if not self._adaptive_armed:
            self._adaptive_armed = True
            # The firmware profile is what is actually running. Remembering the
            # desired profile here made Silent + Quiet look applied while Balance stayed on.
            self._plat_last = live
        if choice == live and not cooling and not self._plat_cool:
            self._plat_last = choice
            return
        if choice == self._plat_last and cooling == self._plat_cool and choice == live:
            return
        self._plat_ts = now
        previous = self._plat_last
        previous_cool = self._plat_cool
        self._plat_last = choice
        self._plat_cool = cooling

        def done(ok: bool, msg: str) -> None:
            if ok:
                self._set_status(f"{source}: {POWER_RULE_LABELS.get(rule, rule)} · {msg}")
            else:
                self._plat_last = previous
                self._plat_cool = previous_cool
                self._set_status(msg)

        if not schedule_apply(lambda: apply_platform_choice(choice, cooling), done):
            self._plat_last = previous
            self._plat_cool = previous_cool

    def _set_status(self, msg: str) -> None:
        self.toast.set_text(msg)
        if needs_daemon_install(msg):
            self.toast.set_text("Daemon is not running. Use Repair and start daemon, then try again.")
            self._sync_daemon_banner(force=True, message=msg)

    def _sync_daemon_banner(self, force: bool = False, message: str = "") -> None:
        show = force or needs_daemon_install(message)
        if show and is_auto_cpufreq_daemon_active() and not needs_daemon_install(message):
            show = False
        if show:
            self.daemon_banner.show_all()
        else:
            self.daemon_banner.hide()

    def _on_install_daemon(self, _btn: Gtk.Button) -> None:
        self.daemon_install_btn.set_sensitive(False)

        def work():
            return run_install_daemon()

        def done(ok: bool, msg: str) -> None:
            self.daemon_install_btn.set_sensitive(True)
            self._set_status(msg)
            if ok:
                self._sync_daemon_banner(force=False)
                self.on_changed()
            else:
                self._sync_daemon_banner(force=True, message=msg)

        if not schedule_apply(work, done):
            self.daemon_install_btn.set_sensitive(True)
            self._set_status("A password dialog is already open. Finish that first.")
            return
        self._set_status("Waiting for the password dialog (installing daemon)…")

    def _on_profile_pick(self, mode: str, previous: str) -> None:
        self._apply_choice(lambda: run_profile(mode), previous, self.profile_tiles)

    def _on_turbo_pick(self, mode: str, previous: str) -> None:
        self._apply_choice(lambda: run_turbo(mode), previous, self.turbo_choice)

    def _apply_choice(self, work, previous: str, choice) -> None:
        def done(ok: bool, msg: str) -> None:
            if ok:
                self.on_changed()
                self._set_status(msg)
                self._sync_daemon_banner(force=False)
            else:
                choice.set_current(previous)
                self._set_status(msg)
                if needs_daemon_install(msg):
                    self._sync_daemon_banner(force=True, message=msg)

        if not schedule_apply(work, done):
            choice.set_current(previous)
            self._set_status("A password dialog is already open. Finish that first.")
            return
        self._set_status("Waiting for the password dialog…")

    def _on_save_conf(self, _widget) -> None:
        if self._updating:
            return
        charger = {
            "governor": _combo_value(self.charger_gov[1], GOVERNORS),
            "turbo": _combo_value(self.charger_turbo[1], TURBO_MODES),
        }
        battery = {
            "governor": _combo_value(self.battery_gov[1], GOVERNORS),
            "turbo": _combo_value(self.battery_turbo[1], TURBO_MODES),
        }

        def work():
            return save_daemon_config(charger, battery)

        def done(ok: bool, msg: str) -> None:
            self._set_status(msg)
            if ok:
                self.refresh_status()

        if not schedule_apply(work, done):
            self._set_status("A password dialog is already open. Finish that first.")
            return
        self._set_status("Waiting for the password dialog…")

    def _on_apply_charge(self, _btn) -> None:
        if self._updating:
            return
        pct = int(self.charge_spin.get_value())

        def done(ok: bool, msg: str) -> None:
            self._set_status(msg)
            if ok:
                self.refresh_status()

        schedule_apply(lambda: set_charge_limit(pct), done)

    def _on_preset_charge(self, _btn, pct: int) -> None:
        if self._updating:
            return
        self.charge_spin.set_value(pct)

        def done(ok: bool, msg: str) -> None:
            self._set_status(msg)
            if ok:
                self.refresh_status()

        schedule_apply(lambda: set_charge_limit(pct), done)

    def _sync_preset_highlight(self, limit: int | None) -> None:
        for pct, btn in self._preset_buttons.items():
            ctx = btn.get_style_context()
            if limit is not None and pct == limit:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")

    def refresh_status(self) -> None:
        prof = read_samsung_mode()
        turbo = read_turbo_override()
        self._updating = True
        self.profile_tiles.set_current(prof)
        self.turbo_choice.set_current(turbo)
        limit = read_charge_limit()
        if limit is not None and hasattr(self, "charge_spin"):
            self.charge_spin.set_value(limit)
        if hasattr(self, "limit_label"):
            self.limit_label.set_text(
                f"Active charge limit: {limit}%" if limit is not None else "Charge limit unavailable"
            )
        self._sync_preset_highlight(limit)
        if turbo == "auto":
            self.turbo_choice.reflect_live_turbo(read_turbo_live())
        self._updating = False
        footer = read_status_footer()
        self.status_profile.set_markup(f"Profile: <b>{footer['profile']}</b>")
        self.status_turbo.set_markup(f"Turbo: <b>{footer['turbo']}</b>")
        self.status_charge.set_markup(f"Charging: <b>{footer['charge']}</b>")
        self.status_gov_c.set_markup(f"Gov (charger): <b>{footer['gov_charger']}</b>")
        self.status_gov_b.set_markup(f"Gov (battery): <b>{footer['gov_battery']}</b>")
        if is_auto_cpufreq_daemon_active():
            self._sync_daemon_banner(force=False)
        else:
            self._sync_daemon_banner(force=True)
        self._paint_live()


class CompactFloatBar(Gtk.Window):
    def __init__(self, on_applied, open_window) -> None:
        super().__init__(title="CPU Power")
        self.set_decorated(True)
        self.set_resizable(False)
        self.set_keep_above(True)
        self.set_skip_taskbar_hint(True)
        self.set_type_hint(Gdk.WindowTypeHint.UTILITY)
        self.on_applied = on_applied
        self.open_window = open_window
        if LOCAL_ICON.is_file():
            self.set_icon_from_file(str(LOCAL_ICON))

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        for label, mode in (("Auto", "default"), ("Save", "powersave"), ("Perf", "performance"), ("Turbo off", "turbo-never")):
            btn = Gtk.Button(label=label)
            if mode == "turbo-never":
                btn.connect("clicked", self._on_turbo_off)
            else:
                btn.connect("clicked", self._on_mode, mode)
            box.pack_start(btn, True, True, 0)
        for label, cb in (("…", open_window), ("Hide", self.hide)):
            btn = Gtk.Button(label=label)
            btn.connect("clicked", lambda _b, f=cb: f())
            box.pack_start(btn, False, False, 0)
        self.add(box)
        self._place_top_right()
        self.connect("delete-event", lambda *_: self.hide() or True)

    def _place_top_right(self) -> None:
        display = Gdk.Display.get_default()
        if not display:
            return
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if not monitor:
            return
        geo = monitor.get_geometry()
        self.move(geo.x + geo.width - 520, geo.y + 8)

    def _on_mode(self, _btn, mode: str) -> None:
        def done(ok: bool, _msg: str) -> None:
            if ok:
                self.on_applied()

        schedule_apply(lambda: run_profile(mode), done)

    def _on_turbo_off(self, _btn) -> None:
        def done(ok: bool, _msg: str) -> None:
            if ok:
                self.on_applied()

        schedule_apply(lambda: run_turbo("never"), done)


class CpuPowerApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self.window: ControlWindow | None = None
        self.indicator = None
        self.xapp_icon = None
        self.tray_popup: TrayPopupWindow | None = None
        self._tray_menu: Gtk.Menu | None = None
        self._tray_status_item: Gtk.MenuItem | None = None
        self._profile_items: dict[str, Gtk.RadioMenuItem] = {}
        self._turbo_items: dict[str, Gtk.RadioMenuItem] = {}
        self._charge_items: dict[int, Gtk.RadioMenuItem] = {}
        self._charge_root: Gtk.MenuItem | None = None
        self._charge_none: Gtk.RadioMenuItem | None = None
        self._tray_updating = False
        self._tray_ok = False
        self._services_ready = False

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        apply_app_theme()

    def do_command_line(self, command_line) -> int:
        # Runs in the already-running instance, including launches from the app menu.
        self._ensure_services()
        args = command_line.get_arguments()[1:]
        if "--window" in args or "--tray-only" not in args:
            self._present_window()
        return 0

    def do_activate(self) -> None:
        self._present_window()

    def _ensure_services(self) -> None:
        if self._services_ready:
            return
        self._services_ready = True
        try:
            save_battery_icon(load_battery_icon())
        except OSError:
            pass
        try:
            get_keyboard()
        except Exception:
            pass
        try:
            ensure_tray_icon()
        except (OSError, cairo.Error):
            pass
        self._build_tray_menu()
        self._tray_ok = self._build_tray()
        if self._tray_ok:
            self.hold()

    def _present_window(self) -> None:
        if self.tray_popup is not None:
            self.tray_popup.hide()
        if self.window is None:
            self.window = ControlWindow(on_changed=self._on_changed)
            self.window.connect("destroy", self._on_window_destroy)
            self.add_window(self.window)
        self.window.set_skip_taskbar_hint(False)
        self.window.set_skip_pager_hint(False)
        self.window.set_decorated(True)
        self.window.present()
        self.window.deiconify()
        self.window._start_live_updates()
        self.window.refresh_status()

    def _on_window_destroy(self, _widget) -> None:
        self.window = None
        if not self._tray_ok:
            self.quit()

    def _quit_from_tray(self) -> None:
        if self.window is not None:
            if hasattr(self.window, "_stop_live_updates"):
                self.window._stop_live_updates()
            try:
                self.remove_window(self.window)
            except GLib.Error:
                pass
            self.window.destroy()
            self.window = None
        if self.tray_popup is not None:
            self.tray_popup.hide()
        self.quit()

    def _on_changed(self) -> None:
        self._sync_tray()
        if self.window:
            self.window.refresh_status()

    def _sync_tray(self) -> None:
        if self.tray_popup and self.tray_popup.get_visible():
            self.tray_popup.refresh()
        self._sync_tray_menu()

    def _build_tray_menu(self) -> None:
        menu = Gtk.Menu()
        menu.get_style_context().add_class("tray-menu")
        status = Gtk.MenuItem(label="CPU Power")
        status.set_sensitive(False)
        menu.append(status)
        self._tray_status_item = status
        menu.append(Gtk.SeparatorMenuItem())

        first = None
        for mode, label in (
            ("adaptive", "Adaptive"),
            ("silent", "Silent"),
            ("quiet", "Quiet"),
            ("balance", "Balance"),
            ("power", "Power"),
        ):
            if first is None:
                item = Gtk.RadioMenuItem.new_with_label([], label)
                first = item
            else:
                item = Gtk.RadioMenuItem.new_with_label_from_widget(first, label)
            item.connect("toggled", self._on_tray_profile, mode)
            menu.append(item)
            self._profile_items[mode] = item

        menu.append(Gtk.SeparatorMenuItem())

        turbo_root = Gtk.MenuItem(label="Turbo")
        turbo_menu = Gtk.Menu()
        turbo_root.set_submenu(turbo_menu)
        first = None
        for mode, label in (("auto", "Auto"), ("never", "Off"), ("always", "On")):
            if first is None:
                item = Gtk.RadioMenuItem.new_with_label([], label)
                first = item
            else:
                item = Gtk.RadioMenuItem.new_with_label_from_widget(first, label)
            item.connect("toggled", self._on_tray_turbo, mode)
            turbo_menu.append(item)
            self._turbo_items[mode] = item
        menu.append(turbo_root)

        charge_root = Gtk.MenuItem(label="Charge limit")
        charge_menu = Gtk.Menu()
        charge_root.set_submenu(charge_menu)
        self._charge_root = charge_root
        none_item = Gtk.RadioMenuItem.new_with_label([], "")
        none_item.set_no_show_all(True)
        charge_menu.append(none_item)
        self._charge_none = none_item
        first = none_item
        for pct, label in ((100, "Off"), (90, "90%"), (80, "80%"), (70, "70%"), (60, "60%"), (50, "50%")):
            item = Gtk.RadioMenuItem.new_with_label_from_widget(first, label)
            item.connect("toggled", self._on_tray_charge, pct)
            charge_menu.append(item)
            self._charge_items[pct] = item
        menu.append(charge_root)

        menu.append(Gtk.SeparatorMenuItem())
        open_item = Gtk.MenuItem(label="Open app")
        open_item.connect("activate", lambda *_: self._present_window())
        menu.append(open_item)
        quit_item = Gtk.MenuItem(label="Quit")
        quit_item.connect("activate", lambda *_: self._quit_from_tray())
        menu.append(quit_item)

        menu.connect("show", lambda *_: self._sync_tray_menu())
        menu.show_all()
        none_item.hide()
        self._tray_menu = menu
        self._sync_tray_menu()

    def _sync_tray_menu(self) -> None:
        if self._tray_menu is None or self._tray_status_item is None:
            return
        live = read_live()
        bits = ["Running" if is_auto_cpufreq_daemon_active() else "Daemon off"]
        if live.get("freq_ghz"):
            bits.append(f"{live['freq_ghz']:.1f} GHz")
        if live.get("temp") is not None:
            bits.append(f"{live['temp']:.0f}°")
        self._tray_updating = True
        self._tray_status_item.set_label("  ·  ".join(bits))
        mode = read_samsung_mode()
        for key, item in self._profile_items.items():
            item.set_active(key == mode)
        turbo = read_turbo_override()
        for key, item in self._turbo_items.items():
            item.set_active(key == turbo)
        limit = read_charge_limit()
        if self._charge_root is not None:
            self._charge_root.set_sensitive(limit is not None)
            if limit is None:
                self._charge_root.set_label("Charge limit")
            elif limit >= 100:
                self._charge_root.set_label("Charge limit")
            else:
                self._charge_root.set_label(f"Charge limit · {limit}%")
        matched = limit in self._charge_items if limit is not None else False
        for pct, item in self._charge_items.items():
            item.set_active(matched and pct == limit)
        if not matched and self._charge_none is not None:
            self._charge_none.set_active(True)
        self._tray_updating = False

    def _on_tray_profile(self, item: Gtk.RadioMenuItem, mode: str) -> None:
        if self._tray_updating or not item.get_active() or read_samsung_mode() == mode:
            return

        def done(ok: bool, _msg: str) -> None:
            self._sync_tray_menu()
            if ok:
                self._on_changed()

        schedule_apply(lambda: run_profile(mode), done)

    def _on_tray_turbo(self, item: Gtk.RadioMenuItem, mode: str) -> None:
        if self._tray_updating or not item.get_active() or read_turbo_override() == mode:
            return

        def done(ok: bool, _msg: str) -> None:
            self._sync_tray_menu()
            if ok:
                self._on_changed()

        schedule_apply(lambda: run_turbo(mode), done)

    def _on_tray_charge(self, item: Gtk.RadioMenuItem, pct: int) -> None:
        if self._tray_updating or not item.get_active() or read_charge_limit() == pct:
            return

        def done(ok: bool, _msg: str) -> None:
            self._sync_tray_menu()
            if ok:
                self._on_changed()

        schedule_apply(lambda: set_charge_limit(pct), done)

    def _build_tray(self) -> bool:
        try:
            self._icon_path = str(ensure_tray_icon())
        except (OSError, cairo.Error):
            self._icon_path = str(TRAY_ICON if TRAY_ICON.is_file() else LOCAL_ICON)
        if self._try_xapp_tray():
            GLib.timeout_add(700, self._ayatana_if_xapp_missing)
            return True
        return self._build_ayatana_tray()

    def _try_xapp_tray(self) -> bool:
        try:
            gi.require_version("XApp", "1.0")
            from gi.repository import XApp
        except (ValueError, ImportError):
            return False
        icon = XApp.StatusIcon.new()
        icon.set_name(APP_ID)
        icon.set_icon_name(self._icon_path)
        icon.set_tooltip_text("CPU Power")
        icon.connect("button-press-event", self._on_xapp_button)
        icon.connect("state-changed", self._on_xapp_state)
        self.xapp_icon = icon
        return True

    def _on_xapp_state(self, icon, state) -> None:
        try:
            gi.require_version("XApp", "1.0")
            from gi.repository import XApp
        except (ValueError, ImportError):
            return
        if state != XApp.StatusIconState.NO_SUPPORT:
            return
        if self.indicator is None:
            self._build_ayatana_tray()

    def _ayatana_if_xapp_missing(self) -> bool:
        if self.indicator is not None or self.xapp_icon is None:
            return False
        try:
            gi.require_version("XApp", "1.0")
            from gi.repository import XApp
        except (ValueError, ImportError):
            self._build_ayatana_tray()
            return False
        if self.xapp_icon.get_state() == XApp.StatusIconState.NO_SUPPORT:
            self._build_ayatana_tray()
        return False

    def _on_xapp_button(self, _icon, x: int, y: int, button: int, time: int, panel_position: int) -> None:
        if button != 1:
            self._present_window()
            return
        if self._tray_menu is None:
            return
        self._sync_tray_menu()

        def place(menu, _x, _y, _data):
            width, height = menu.get_size_request()
            req = menu.get_preferred_size()[1]
            mw = req.width or width or 180
            mh = req.height or height or 220
            px, py = int(x), int(y)
            if int(panel_position) == 2:
                return px, py, False
            return px - mw // 2, py - mh, False

        self._tray_menu.popup(None, None, place, None, button, time)

    def _build_ayatana_tray(self) -> bool:
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
        except (ValueError, ImportError):
            return False
        self.indicator = AppIndicator.Indicator.new(
            APP_ID,
            self._icon_path,
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_menu(self._tray_menu)
        self.indicator.set_title("CPU Power")
        if hasattr(self.indicator, "set_icon_full"):
            self.indicator.set_icon_full(self._icon_path, "CPU Power")
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        return True

def main() -> int:
    app = CpuPowerApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
