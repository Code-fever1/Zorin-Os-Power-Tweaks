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
  background-color: #151b28;
  border: 1px solid rgba(72, 130, 255, 0.55);
  border-radius: 18px;
  box-shadow: 0 0 28px rgba(45, 120, 255, 0.35), 0 12px 40px rgba(0, 0, 0, 0.55);
  padding: 14px 16px 16px 16px;
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
  background: rgba(20, 30, 52, 0.95);
  border: 1px solid rgba(70, 110, 200, 0.35);
  border-radius: 10px;
  padding: 6px 10px;
  min-width: 0;
  min-height: 0;
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
  background: rgba(14, 22, 40, 0.95);
  border: 1px solid rgba(55, 85, 150, 0.45);
  border-radius: 12px;
  padding: 8px 6px;
  min-width: 62px;
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
        pad = 4.0
        r = 10.0
        cr.set_source_rgba(0.12, 0.22, 0.48, 1)
        cr.new_sub_path()
        cr.arc(48 - pad - r, pad + r, r, -math.pi / 2, 0)
        cr.arc(48 - pad - r, 48 - pad - r, r, 0, math.pi / 2)
        cr.arc(pad + r, 48 - pad - r, r, math.pi / 2, math.pi)
        cr.arc(pad + r, pad + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.fill()
        cr.set_source_rgba(0.35, 0.65, 1.0, 0.85)
        cr.set_line_width(2.2)
        cr.stroke()
        cr.set_source_rgb(1, 1, 1)
        _draw_lightning(cr, 24, 24, 22)
        cr.fill()
        surface.write_to_png(str(path))
    return TRAY_ICON


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
    ("🍃", "Eco", "silent"),
    ("⚖", "Balanced", "balance"),
    ("⏱", "Performance", "power"),
    ("✦", "Turbo", "turbo"),
)


class TrayPopupWindow(Gtk.Window):
    def __init__(self, app: "CpuPowerApp") -> None:
        super().__init__(title="CPU Power")
        self.app = app
        self._updating = False
        self._profile_btns: dict[str, Gtk.Button] = {}
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_skip_taskbar_hint(True)
        self.set_keep_above(True)
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_default_size(520, 340)
        self.get_style_context().add_class("tray-popup")
        self.connect("delete-event", lambda *_: self.hide() or True)
        self.connect("key-press-event", self._on_key)

        outer = Gtk.EventBox()
        shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        shell.get_style_context().add_class("tray-shell")
        shell.set_border_width(0)
        outer.add(shell)
        self.add(outer)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bolt = Gtk.Label(label="⚡")
        title = Gtk.Label(label="CPU Power")
        title.get_style_context().add_class("tray-title")
        title.set_halign(Gtk.Align.START)
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title_row.pack_start(bolt, False, False, 0)
        title_row.pack_start(title, False, False, 0)
        title_box.pack_start(title_row, False, False, 0)
        self.status_lbl = Gtk.Label(label="● Running")
        self.status_lbl.set_halign(Gtk.Align.START)
        self.status_lbl.get_style_context().add_class("tray-status-ok")
        title_box.pack_start(self.status_lbl, False, False, 0)
        header.pack_start(title_box, True, True, 0)
        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for icon, cb in (("📊", self._open_monitor), ("⚙", self._open_settings), ("✕", self.hide)):
            btn = Gtk.Button(label=icon)
            btn.get_style_context().add_class("tray-icon-btn")
            btn.connect("clicked", lambda _b, f=cb: f())
            actions.pack_start(btn, False, False, 0)
        header.pack_start(actions, False, False, 0)
        shell.pack_start(header, False, False, 0)

        mid = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        mid.get_style_context().add_class("tray-mid-card")
        self.gauge = TrayCpuGauge()
        mid.pack_start(self.gauge, False, False, 0)
        stats = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        stats.set_valign(Gtk.Align.CENTER)
        self.stat_freq = Gtk.Label(label="🧠 — GHz", halign=Gtk.Align.START)
        self.stat_temp = Gtk.Label(label="🌡 — °C", halign=Gtk.Align.START)
        self.stat_watts = Gtk.Label(label="⚡ — W", halign=Gtk.Align.START)
        for lbl in (self.stat_freq, self.stat_temp, self.stat_watts):
            lbl.get_style_context().add_class("tray-stat")
            stats.pack_start(lbl, False, False, 0)
        mid.pack_start(stats, False, False, 0)
        prof_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        prof_box.set_hexpand(True)
        pl = Gtk.Label(label="☰  Profile", halign=Gtk.Align.START)
        pl.get_style_context().add_class("tray-profile-label")
        prof_box.pack_start(pl, False, False, 0)
        prof_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        prof_row.set_homogeneous(True)
        for icon, name, mode in TRAY_PROFILES:
            btn = Gtk.Button()
            btn.get_style_context().add_class("tray-profile-btn")
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            ic = Gtk.Label(label=icon)
            ic.get_style_context().add_class("tray-profile-icon")
            nm = Gtk.Label(label=name)
            nm.get_style_context().add_class("tray-profile-name")
            inner.pack_start(ic, False, False, 0)
            inner.pack_start(nm, False, False, 0)
            btn.add(inner)
            btn.connect("clicked", self._on_profile, mode)
            prof_row.pack_start(btn, True, True, 0)
            self._profile_btns[mode] = btn
        prof_box.pack_start(prof_row, True, True, 0)
        mid.pack_start(prof_box, True, True, 0)
        shell.pack_start(mid, False, False, 0)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        turbo_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        turbo_card.get_style_context().add_class("tray-bottom-card")
        turbo_card.set_size_request(200, -1)
        tl = Gtk.Label(label="⚡  Turbo", halign=Gtk.Align.START)
        tl.get_style_context().add_class("tray-field-label")
        turbo_card.pack_start(tl, False, False, 0)
        self.turbo_combo = Gtk.ComboBoxText()
        for label, mode in (("Auto", "auto"), ("Off", "never"), ("On", "always")):
            self.turbo_combo.append_text(label)
        self.turbo_combo.get_style_context().add_class("tray-combo")
        self.turbo_combo.connect("changed", self._on_turbo_changed)
        turbo_card.pack_start(self.turbo_combo, False, False, 0)
        bottom.pack_start(turbo_card, True, True, 0)

        charge_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        charge_card.get_style_context().add_class("tray-bottom-card")
        charge_card.set_hexpand(True)
        cl = Gtk.Label(label="🔋  Charge Limit", halign=Gtk.Align.START)
        cl.get_style_context().add_class("tray-field-label")
        charge_card.pack_start(cl, False, False, 0)
        charge_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.charge_on = Gtk.Switch()
        self.charge_on.connect("notify::active", self._on_charge_toggle)
        charge_row.pack_start(self.charge_on, False, False, 0)
        minus = Gtk.Button(label="−")
        minus.get_style_context().add_class("tray-step-btn")
        minus.connect("clicked", self._charge_step, -1)
        plus = Gtk.Button(label="+")
        plus.get_style_context().add_class("tray-step-btn")
        plus.connect("clicked", self._charge_step, 1)
        self.charge_pct = Gtk.Label(label="80%")
        self.charge_pct.get_style_context().add_class("tray-stat")
        charge_row.pack_start(minus, False, False, 0)
        charge_row.pack_start(self.charge_pct, False, False, 0)
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

    def refresh(self) -> None:
        live = read_live()
        self.gauge.set_value(float(live["cpu_pct"]))
        freq = f"{live['freq_ghz']:.2f} GHz" if live["freq_ghz"] else "—"
        temp = f"{live['temp']:.0f} °C" if live["temp"] is not None else "—"
        watts = f"{live['rate_w']:.1f} W" if live["rate_w"] is not None else "—"
        self.stat_freq.set_text(f"🧠  {freq}")
        self.stat_temp.set_text(f"🌡  {temp}")
        self.stat_watts.set_text(f"⚡  {watts}")
        if is_auto_cpufreq_daemon_active():
            self.status_lbl.set_text("● Running")
            self.status_lbl.get_style_context().add_class("tray-status-ok")
            self.status_lbl.get_style_context().remove_class("tray-status-bad")
        else:
            self.status_lbl.set_text("● Daemon off")
            self.status_lbl.get_style_context().add_class("tray-status-bad")
            self.status_lbl.get_style_context().remove_class("tray-status-ok")
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

    def present_near_tray(self) -> None:
        self.refresh()
        self.show_all()
        w, h = 520, 340
        self.resize(w, h)
        display = Gdk.Display.get_default()
        if not display:
            self.present()
            return
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if not monitor:
            self.present()
            return
        geo = monitor.get_geometry()
        x = geo.x + geo.width - w - 16
        y = geo.y + geo.height - h - 56
        self.move(max(geo.x + 8, x), max(geo.y + 8, y))
        self.present()
        self.grab_focus()


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


def run_pkexec(args: list[str], timeout: int = 120) -> tuple[bool, str]:
    # pkexec has no --action-id flag. The helper path matches the polkit rule,
    # which shows the password dialog.
    if PRIV_HELPER.is_file():
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
    try:
        subprocess.run(
            ["systemctl", "start", "auto-cpufreq.service"],
            capture_output=True,
            timeout=15,
            check=False,
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
        return "performance"
    if previous == "low-power" and load <= 14 and temp < 52:
        return "low-power"
    if previous == "quiet" and 8 < load < 28 and temp < 72:
        return "quiet"
    if previous == "balanced" and 15 < load < 58 and temp < 80:
        return "balanced"
    return raw


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


def apply_platform_choice(platform: str) -> tuple[bool, str]:
    kind = PLATFORM_KIND.get(platform)
    if not kind:
        return False, f"Unknown platform profile: {platform}"
    ok, msg = run_pkexec(["samsung-mode", "apply", platform, kind])
    if not ok and "Rejected" in msg:
        ok, msg = run_pkexec(["samsung-mode", "platform", platform])
    if not ok:
        return ok, msg
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
        return "Off"
    if override == "always":
        return "On"
    turbo_live = read_turbo_live() if turbo_live is None else turbo_live
    if turbo_live is True:
        return "Auto · On"
    if turbo_live is False:
        return "Auto · Off"
    return "Auto"


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
        self.phase = 0.0
        self.connect("draw", self._draw)
        GLib.timeout_add(33, self._animate)

    def set_reading(self, percent: float | None, charging: bool, powersave: bool) -> None:
        self.percent = 0.0 if percent is None else max(0.0, min(100.0, float(percent)))
        self.charging = charging
        self.powersave = powersave

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

        if self.charging:
            self._bolt(cr, body_x + body_w / 2, body_y + body_h / 2, min(body_w, body_h) * 0.28)

        label = f"{self.shown:.0f}%"
        cr.set_source_rgb(1, 1, 1)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(max(16, body_w * 0.22))
        ext = cr.text_extents(label)
        cr.move_to(body_x + (body_w - ext.width) / 2, body_y + body_h * 0.58)
        cr.show_text(label)
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


class ControlWindow(Gtk.Window):
    NAV = (
        ("Dashboard", "dashboard"),
        ("CPU", "cpu"),
        ("Battery", "battery"),
        ("Memory", "memory"),
        ("Rules", "rules"),
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
        self._plat_ts = 0.0
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
        self.show_all()
        self._show_page("dashboard")
        self.refresh_status()
        self._sync_daemon_banner(force=not is_auto_cpufreq_daemon_active())
        GLib.timeout_add_seconds(2, self._tick)

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
            ("Auto rules", "page", "rules"),
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
            label="Adaptive lets auto-cpufreq follow the charger and battery rules. Power saver and Performance stay on until you pick Adaptive again.",
            wrap=True,
            halign=Gtk.Align.START,
        )
        hint.get_style_context().add_class("cpu-card-desc")
        card.pack_start(hint, False, False, 0)
        card.pack_start(Gtk.Label(label="Turbo boost", halign=Gtk.Align.START), False, False, 0)
        note = Gtk.Label(
            label="Auto keeps turbo on while the CPU is busy. It turns turbo off when the laptop is idle, and on AC power also when it is warm and idle (about 65–70°C).",
            wrap=True,
            halign=Gtk.Align.START,
        )
        note.get_style_context().add_class("cpu-card-desc")
        card.pack_start(note, False, False, 0)
        card.pack_start(self.turbo_choice.box, False, False, 0)
        box.pack_start(card, False, False, 0)
        return outer

    def _page_battery(self) -> Gtk.Widget:
        outer, box = self._page_shell("Battery", "Charge level, health, and charge limit.")
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
        return outer

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

    def _page_rules(self) -> Gtk.Widget:
        outer, box = self._page_shell(
            "Rules",
            "Used while the CPU profile is Adaptive. Power save stays in Silent and Quiet. Adaptive can use all four Samsung modes.",
        )
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.get_style_context().add_class("cpu-card")
        card.set_border_width(14)
        self.rule_charger = self._rule_combo(self.power_rules["charger"], self._on_rules_changed)
        self.rule_battery = self._rule_combo(self.power_rules["battery"], self._on_rules_changed)
        for title, combo in (("On charger", self.rule_charger), ("On battery", self.rule_battery)):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            label = Gtk.Label(label=title, halign=Gtk.Align.START)
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
        box.pack_start(card, False, False, 0)
        return outer

    def _on_rules_changed(self, _widget) -> None:
        if self._updating or not hasattr(self, "rule_battery"):
            return
        self.power_rules = {
            "charger": normalize_power_rule(self.rule_charger.get_active_id(), "adaptive"),
            "battery": normalize_power_rule(self.rule_battery.get_active_id(), "powersave"),
        }
        save_power_rules(self.power_rules)
        self._plat_ts = 0.0
        self._set_status("Rules saved. Adaptive will follow them.")

    def _page_scheduler(self) -> Gtk.Widget:
        outer, box = self._page_shell(
            "Scheduler",
            "When a switch is on, that rule replaces the Rules page for that situation. Same choices: Adaptive, Power save, or a forced Samsung mode.",
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
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        card.get_style_context().add_class("cpu-card")
        card.set_border_width(14)
        note = Gtk.Label(
            label="Opening this window does not need a password. Applying CPU, memory, turbo, charge limit, or rules does. "
            "Re-run install-system-helper.sh once after updating so memory controls work. "
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
        self._maybe_adaptive()
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
        self.dash_battery.set_reading(live["percent"], charging, powersave)
        self.battery_icon.set_reading(live["percent"], charging, powersave)
        if live["percent"] is not None and live["percent"] < 20:
            color_note = "Low battery — shown in red"
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
        choice = platform_for_rule(
            rule,
            load,
            self.live.get("temp"),
            bool(self.live.get("plugged")),
            self._plat_last,
        )
        if choice == self._plat_last or choice == read_platform_profile():
            self._plat_last = choice
            return
        self._plat_ts = now
        previous = self._plat_last
        self._plat_last = choice

        def done(ok: bool, msg: str) -> None:
            if ok:
                self._set_status(f"{source}: {POWER_RULE_LABELS.get(rule, rule)} · {msg}")
            else:
                self._plat_last = previous
                self._set_status(msg)

        if not schedule_apply(lambda: apply_platform_choice(choice), done):
            self._plat_last = previous

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
        self.tray_popup: TrayPopupWindow | None = None
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
            ensure_tray_icon()
        except (OSError, cairo.Error):
            pass
        self.tray_popup = TrayPopupWindow(self)
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
        self.window.refresh_status()

    def _on_window_destroy(self, _widget) -> None:
        self.window = None
        if not self._tray_ok:
            self.quit()

    def _on_changed(self) -> None:
        self._sync_tray()
        if self.window:
            self.window.refresh_status()

    def _sync_tray(self) -> None:
        if self.tray_popup and self.tray_popup.get_visible():
            self.tray_popup.refresh()

    def _show_tray_popup(self) -> bool:
        if self.tray_popup:
            self.tray_popup.present_near_tray()
        return False

    def _on_tray_menu_open(self, menu: Gtk.Menu) -> None:
        menu.hide()
        GLib.idle_add(self._show_tray_popup)

    def _build_tray(self) -> bool:
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
        except (ValueError, ImportError):
            return False

        try:
            icon_path = str(ensure_tray_icon())
        except (OSError, cairo.Error):
            icon_path = str(TRAY_ICON if TRAY_ICON.is_file() else LOCAL_ICON)

        self.indicator = AppIndicator.Indicator.new(
            APP_ID,
            icon_path,
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        menu = Gtk.Menu()
        item = Gtk.MenuItem(label="CPU Power")
        menu.append(item)
        menu.connect("map", self._on_tray_menu_open)
        menu.show_all()
        self.indicator.set_menu(menu)
        self.indicator.set_title("CPU Power")
        if hasattr(self.indicator, "set_icon_full"):
            self.indicator.set_icon_full(icon_path, "CPU Power")
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        return True

def main() -> int:
    app = CpuPowerApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
