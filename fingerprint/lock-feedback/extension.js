import Gio from 'gi://Gio'
import * as Main from 'resource:///org/gnome/shell/ui/main.js'
import * as GdmUtil from 'resource:///org/gnome/shell/gdm/util.js'
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js'

export default class LockFingerprintFeedback extends Extension {
  enable() {
    this._sub = 0
    this._sub = Gio.DBus.system.signal_subscribe(
      'net.reactivated.Fprint',
      'net.reactivated.Fprint.Device',
      'VerifyStatus',
      null,
      null,
      Gio.DBusSignalFlags.NONE,
      this._onVerify.bind(this))
  }

  disable() {
    if (this._sub) {
      Gio.DBus.system.signal_unsubscribe(this._sub)
      this._sub = 0
    }
  }

  _onVerify(_bus, _sender, _path, _iface, _signal, params) {
    let result = ''
    try {
      result = params.get_child_value(0).unpack()
    } catch (e) {
      return
    }
    if (result === 'verify-match')
      this._showLoggingIn()
  }

  _showLoggingIn() {
    const prompt = Main.screenShield?._dialog?._authPrompt
    if (!prompt)
      return
    try {
      prompt.setMessage('Fingerprint recognized — logging in', GdmUtil.MessageType.INFO)
      prompt.startSpinning()
    } catch (e) {
      log(`lock-fingerprint: ${e}`)
    }
  }
}
