"""Requests from the device bridge (litelink-devices) to the shell, during a LiteOS session.

Some of what LiteOS may ask for has to happen in LiteLink's own screens, shown over
Chromium: the admin password (asked by LiteLink, never typed into the page), the lock
screen, Settings, and "Keep these display settings?". The bridge can't draw those, so
it asks the shell over a Unix socket in the session's private folder (mode 0700, the
litelink user's only; its path reaches the bridge in session.json). One request per
connection: a JSON line in, a JSON line out when it's done.

  {"ask": "admin", "why": "Restart this device"}   -> {"ok": true | false}
  {"do": "lock"}                                    -> {"ok": true}
  {"do": "settings", "page": "sound"}               -> {"ok": true}
  {"do": "display", "output": "HDMI-1", "mode": "1920x1080"} -> {"ok": true, "kept": bool}
"""
import json
import os

from gi.repository import Gio, GLib

from . import system

SETTINGS_PAGES = {"network", "vpn", "bluetooth", "remote", "display", "keyboard", "sound", "time", "mode",
                  "storage", "diag", "about"}


class Requests:
    def __init__(self, app, path):
        self.app = app
        self.path = path
        try:
            os.unlink(path)
        except OSError:
            pass
        self.service = Gio.SocketService()
        self.service.add_address(Gio.UnixSocketAddress.new(path), Gio.SocketType.STREAM,
                                 Gio.SocketProtocol.DEFAULT, None)
        self.service.connect("incoming", self._incoming)
        self.service.start()

    def close(self):
        self.service.stop()
        self.service.close()
        try:
            os.unlink(self.path)
        except OSError:
            pass

    def _incoming(self, _service, conn, _source):
        data_in = Gio.DataInputStream.new(conn.get_input_stream())
        data_in.read_line_async(GLib.PRIORITY_DEFAULT, None, self._line, conn)
        return True

    def _line(self, stream, result, conn):
        try:
            line, _ = stream.read_line_finish_utf8(result)
            req = json.loads(line or "{}")
        except (GLib.Error, ValueError):
            req = {}

        def reply(obj):
            try:
                conn.get_output_stream().write_all((json.dumps(obj) + "\n").encode(), None)
                conn.close(None)
            except GLib.Error:
                pass
        self.handle(req if isinstance(req, dict) else {}, reply)

    def handle(self, req, reply):
        app = self.app
        if req.get("ask") == "admin":
            why = req.get("why") if isinstance(req.get("why"), str) else ""
            app.admin_for_session(why[:120], lambda ok: reply({"ok": bool(ok)}))
        elif req.get("do") == "lock":
            app.lock()
            reply({"ok": True})
        elif req.get("do") == "settings":
            pages = SETTINGS_PAGES | ({"printers"} if system.print_engine() else set())   # only where printing is installed
            page = req.get("page") if req.get("page") in pages else "network"
            app.settings_for_session(page)
            reply({"ok": True})
        elif req.get("do") == "display" and isinstance(req.get("output"), str) and isinstance(req.get("mode"), str):
            app.display_for_session(req["output"], req["mode"], lambda ok, kept: reply({"ok": ok, "kept": kept}))
        else:
            reply({"ok": False})
