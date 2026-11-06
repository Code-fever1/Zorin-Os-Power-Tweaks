import Gio from 'gi://Gio'
import GLib from 'gi://GLib'
import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js'
import * as Main from 'resource:///org/gnome/shell/ui/main.js'

const DEFAULTS = {
  adaptive: { enabled: true, color: '#C084FC' },
  silent: { enabled: true, color: '#2DBA6E' },
  quiet: { enabled: true, color: '#1FA7A0' },
  balance: { enabled: true, color: '#7EB8FF' },
  power: { enabled: true, color: '#3484E4' },
}

const PLATFORM_MODE = {
  'low-power': 'silent',
  quiet: 'quiet',
  balanced: 'balance',
  performance: 'power',
}

export default class CpuPowerBatteryColor extends Extension {
  enable() {
    this._monitor = null
    this._timeoutId = null
    this._pollId = null
    this._timeoutId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1500, () => {
      this._start()
      this._timeoutId = null
      return GLib.SOURCE_REMOVE
    })
  }

  _configDir() {
    return GLib.build_filenamev([GLib.get_home_dir(), '.config', 'zorin-cpu-power'])
  }

  _start() {
    try {
      const dir = Gio.File.new_for_path(this._configDir())
      this._monitor = dir.monitor_directory(Gio.FileMonitorFlags.NONE, null)
      this._monitor.connect('changed', () => this._sync())
    } catch (e) {
      console.error(`[CPU Power] Could not watch mode files: ${e.message}`)
    }
    this._sync()
    this._pollId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 2, () => {
      this._sync()
      return GLib.SOURCE_CONTINUE
    })
  }

  _decode(bytes) {
    if (bytes == null)
      return ''
    if (typeof bytes === 'string')
      return bytes.trim()
    return new TextDecoder().decode(bytes).trim()
  }

  _readText(name) {
    try {
      const path = GLib.build_filenamev([this._configDir(), name])
      return this._decode(GLib.file_get_contents(path)[1])
    } catch (e) {
      return ''
    }
  }

  _readMode() {
    const saved = this._readText('samsung-mode')
    if (saved in DEFAULTS)
      return saved
    try {
      const profile = this._decode(GLib.file_get_contents('/sys/firmware/acpi/platform_profile')[1])
      return PLATFORM_MODE[profile] || 'adaptive'
    } catch (e) {
      return 'adaptive'
    }
  }

  _readSettings() {
    const settings = {}
    for (const key of Object.keys(DEFAULTS))
      settings[key] = { ...DEFAULTS[key] }
    const text = this._readText('battery-icon.json')
    if (!text)
      return settings
    try {
      const raw = JSON.parse(text)
      for (const key of Object.keys(DEFAULTS)) {
        const item = raw[key]
        if (!item || typeof item !== 'object')
          continue
        if (typeof item.color === 'string' && item.color.trim())
          settings[key].color = item.color.trim()
        if (typeof item.enabled === 'boolean')
          settings[key].enabled = item.enabled
      }
    } catch (e) {
      console.error(`[CPU Power] Battery color settings: ${e.message}`)
    }
    return settings
  }

  _getPowerActor() {
    const quickSettings = Main.panel.statusArea.quickSettings
    if (!quickSettings?._indicators)
      return null
    const children = quickSettings._indicators.get_children()
    return children.find(child => {
      const isBattery = actor => {
        if (actor.icon_name && actor.icon_name.includes('battery'))
          return true
        return actor.get_children?.().some(childActor => isBattery(childActor)) ?? false
      }
      return isBattery(child)
    }) ?? null
  }

  _sync() {
    const actor = this._getPowerActor()
    if (!actor)
      return
    const entry = this._readSettings()[this._readMode()]
    if (!entry?.enabled) {
      actor.set_style(null)
      return
    }
    actor.set_style(`color: ${entry.color} !important;`)
  }

  disable() {
    if (this._timeoutId) {
      GLib.source_remove(this._timeoutId)
      this._timeoutId = null
    }
    if (this._pollId) {
      GLib.source_remove(this._pollId)
      this._pollId = null
    }
    if (this._monitor) {
      this._monitor.cancel()
      this._monitor = null
    }
    const actor = this._getPowerActor()
    if (actor)
      actor.set_style(null)
  }
}
