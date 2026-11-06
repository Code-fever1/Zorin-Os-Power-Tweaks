import Gio from 'gi://Gio'
import * as Main from 'resource:///org/gnome/shell/ui/main.js'
import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js'

const IFACE = `
<node>
  <interface name="org.zorin.Fpchoice">
    <method name="Submit">
      <arg type="b" direction="out" name="accepted"/>
    </method>
  </interface>
</node>`

export default class FpchoicePolkit extends Extension {
  enable() {
    this._nameId = 0
    this._dbus = null
    this._dialog = null
    this._sent = false
    this._nameId = Gio.bus_own_name(
      Gio.BusType.SESSION,
      'org.zorin.Fpchoice',
      Gio.BusNameOwnerFlags.REPLACE,
      this._onBus.bind(this),
      null,
      null)
  }

  disable() {
    if (this._dbus) {
      this._dbus.unexport()
      this._dbus = null
    }
    if (this._nameId) {
      Gio.bus_unown_name(this._nameId)
      this._nameId = 0
    }
    this._dialog = null
  }

  _onBus(connection) {
    this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this)
    this._dbus.export(connection, '/org/zorin/Fpchoice')
  }

  Submit() {
    const agent = Main.componentManager?._allComponents?.polkitAgent
    const dialog = agent?._currentDialog
    if (!dialog || dialog._doneEmitted || dialog._completed || !dialog._session)
      return false
    if (!dialog._passwordEntry?.reactive)
      return false
    if (dialog !== this._dialog) {
      this._dialog = dialog
      this._sent = false
    }
    if (this._sent)
      return true
    try {
      dialog._session.response('\u001e')
      this._sent = true
      return true
    } catch (error) {
      log(`fpchoice: ${error}`)
      return false
    }
  }
}
