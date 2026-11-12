import Gio from 'gi://Gio'
import GLib from 'gi://GLib'
import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js'
import * as Main from 'resource:///org/gnome/shell/ui/main.js'

const DEFAULTS = {
  silent: { enabled: true, color: '#2DBA6E' },
  quiet: { enabled: true, color: '#1FA7A0' },
  balance: { enabled: true, color: '#7EB8FF' },
  power: { enabled: true, color: '#3484E4' },
}

const TURBO_DEFAULT = { enabled: true, color: '#FFBE6F' }

const PLATFORM_MODE = {
  'low-power': 'silent',
  quiet: 'quiet',
  balanced: 'balance',
  performance: 'power',
}

export default class CpuPowerBatteryColor extends Extension {
  enable() {
    this._monitor = null
    this._platformMonitor = null
    this._turboMonitor = null
    this._timeoutId = null
    this._pollId = null
    this._painted = []
    this._lastIcon = undefined
    this._lastLabel = undefined
    this._turboBarOn = false
    this._turboBarFlipAt = 0
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
    try {
      const platformFile = Gio.File.new_for_path('/sys/firmware/acpi/platform_profile')
      this._platformMonitor = platformFile.monitor_file(Gio.FileMonitorFlags.NONE, null)
      this._platformMonitor.connect('changed', () => this._sync())
    } catch (e) {
      this._platformMonitor = null
    }
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

  _readPublished() {
    try {
      const path = GLib.build_filenamev([this._configDir(), 'battery-live.json'])
      const file = Gio.File.new_for_path(path)
      const info = file.query_info('time::modified', Gio.FileQueryInfoFlags.NONE, null)
      const age = GLib.get_real_time() / 1000000 - info.get_modification_date_time().to_unix()
      if (age > 45)
        return null
      const raw = JSON.parse(this._readText('battery-live.json'))
      if (!raw || typeof raw !== 'object' || !(raw.mode in DEFAULTS))
        return null
      return raw
    } catch (e) {
      return null
    }
  }

  _readMode() {
    const saved = this._readText('samsung-mode')
    if (saved in DEFAULTS)
      return saved
    try {
      const profile = this._decode(GLib.file_get_contents('/sys/firmware/acpi/platform_profile')[1])
      if (profile in PLATFORM_MODE)
        return PLATFORM_MODE[profile]
    } catch (e) {
      // Fall through when the firmware profile is unreadable.
    }
    return 'balance'
  }

  _readSettings() {
    const settings = {}
    for (const key of Object.keys(DEFAULTS))
      settings[key] = { ...DEFAULTS[key] }
    settings.turbo = { ...TURBO_DEFAULT }
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
      const turbo = raw.turbo && typeof raw.turbo === 'object' ? raw.turbo : raw.adaptive
      if (turbo && typeof turbo === 'object') {
        if (typeof turbo.color === 'string' && turbo.color.trim())
          settings.turbo.color = turbo.color.trim()
        if (typeof turbo.enabled === 'boolean')
          settings.turbo.enabled = raw.turbo ? turbo.enabled : true
      }
    } catch (e) {
      console.error(`[CPU Power] Battery color settings: ${e.message}`)
    }
    return settings
  }

  _readTurboOverride() {
    const text = this._readText('turbo-override')
    if (text === 'always' || text === 'never' || text === 'auto')
      return text
    return ''
  }

  _stableTurboFromHw(rawOn, override) {
    if (override === 'always')
      return true
    if (override === 'never')
      return false
    const now = GLib.get_monotonic_time() / 1e6
    if (rawOn === this._turboBarOn) {
      this._turboBarFlipAt = now
      return this._turboBarOn
    }
    const hold = rawOn ? 2.0 : 0.75
    if (now - this._turboBarFlipAt >= hold) {
      this._turboBarOn = rawOn
      this._turboBarFlipAt = now
    }
    return this._turboBarOn
  }

  _turboIsOn() {
    try {
      const noTurbo = this._decode(GLib.file_get_contents('/sys/devices/system/cpu/intel_pstate/no_turbo')[1])
      if (noTurbo === '0')
        return true
      if (noTurbo === '1')
        return false
    } catch (e) {
      // Some CPUs expose boost instead of intel_pstate.
    }
    try {
      const boost = this._decode(GLib.file_get_contents('/sys/devices/system/cpu/cpufreq/boost')[1])
      return boost === '1'
    } catch (e) {
      return false
    }
  }

  _iconLabel(actor) {
    const parts = []
    try {
      if (actor.icon_name)
        parts.push(String(actor.icon_name))
      if (actor.fallback_icon_name)
        parts.push(String(actor.fallback_icon_name))
      const gicon = actor.gicon
      if (gicon?.get_names)
        parts.push(...gicon.get_names())
      else if (Array.isArray(gicon?.names))
        parts.push(...gicon.names)
    } catch (e) {
      // A destroyed actor or a non-icon can be skipped.
    }
    return parts.join(' ').toLowerCase()
  }

  _iconActors() {
    const quickSettings = Main.panel?.statusArea?.quickSettings
    if (!quickSettings)
      return []
    const found = []
    const seen = new Set()
    const add = actor => {
      if (!actor || seen.has(actor))
        return
      seen.add(actor)
      found.push(actor)
    }
    const percent = this._percentageLabel()
    const walk = (actor, fromSystem) => {
      for (const child of actor.get_children?.() ?? []) {
        if (child === percent)
          continue
        const text = typeof child.text === 'string' ? child.text : ''
        if (text.includes('%'))
          continue
        const label = this._iconLabel(child)
        if (label.includes('battery') && !label.includes('brightness'))
          add(child)
        walk(child, fromSystem)
      }
    }
    if (quickSettings._system)
      walk(quickSettings._system, true)
    if (quickSettings._indicators)
      walk(quickSettings._indicators, false)
    return found
  }

  _percentageLabel() {
    return Main.panel?.statusArea?.quickSettings?._system?._percentageLabel ?? null
  }

  _styleOf(actor) {
    try {
      return actor?.get_style?.() || ''
    } catch (e) {
      return ''
    }
  }

  _hold(actor, style) {
    if (!actor)
      return
    actor._cpuPowerStyle = style || ''
    if (!actor._cpuPowerBound) {
      actor._cpuPowerBound = true
      actor.connect('notify::style', () => {
        if (actor._cpuPowerLock)
          return
        const want = actor._cpuPowerStyle || ''
        if (!want || this._styleOf(actor) === want)
          return
        actor._cpuPowerLock = true
        try {
          actor.set_style(want)
        } catch (e) {
          // The icon can be destroyed while the panel rebuilds.
        }
        actor._cpuPowerLock = false
      })
    }
    if (this._styleOf(actor) === (style || ''))
      return
    actor._cpuPowerLock = true
    try {
      actor.set_style(style)
    } catch (e) {
      // Ignore actors that disappeared mid-update.
    }
    actor._cpuPowerLock = false
  }

  _paint(iconStyle, labelStyle) {
    const icons = this._iconActors()
    this._painted = icons
    for (const actor of icons)
      this._hold(actor, iconStyle || '')
    this._hold(this._percentageLabel(), labelStyle || '')
  }

  _sync() {
    const settings = this._readSettings()
    const published = this._readPublished()
    const mode = published?.mode || this._readMode()
    const entry = settings[mode]
    let turboOn = this._lastTurboOn === true
    if (published && typeof published.turbo_on === 'boolean')
      turboOn = published.turbo_on === true
    this._lastTurboOn = turboOn
    const tankColor = published?.tank_color || entry?.color
    const turboColor = published?.turbo_color || settings.turbo?.color
    const tankEnabled = published ? published.tank_enabled !== false : entry?.enabled
    const iconStyle = tankEnabled && tankColor ? `color: ${tankColor} !important;` : null
    const labelStyle = turboOn && turboColor ? `color: ${turboColor} !important;` : null
    const styleChanged = iconStyle !== this._lastIcon || labelStyle !== this._lastLabel
    const icons = this._iconActors()
    const rebound = icons.some(actor => !actor._cpuPowerBound)
    if (!styleChanged && !rebound)
      return
    this._lastIcon = iconStyle
    this._lastLabel = labelStyle
    this._paint(iconStyle, labelStyle)
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
    if (this._platformMonitor) {
      this._platformMonitor.cancel()
      this._platformMonitor = null
    }
    if (this._turboMonitor) {
      this._turboMonitor.cancel()
      this._turboMonitor = null
    }
    this._paint(null, null)
  }
}
