"""LiteLink shell: the full-screen desktop.

One window holds everything. The wallpaper, dock and desktop icons form the
base; flyouts, dialogs and system screens are layers stacked on top inside the
same window, so the look never depends on the window manager.
"""
import math
import os
import subprocess
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import system, ui  # noqa: E402
from .config import WEB_PROTOCOLS, Config, has_credentials  # noqa: E402

# Sky wallpaper: (offset, colour) stops and soft cloud ellipses (cx, cy, rx, ry, alpha)
SKY = [(0.0, "#1E4E9C"), (0.38, "#3D7ACC"), (0.72, "#8DB9EA"), (1.0, "#D6E7F8")]
CLOUDS = [(0.78, 0.30, 420, 120, 0.55), (0.30, 0.72, 520, 140, 0.50), (0.62, 0.86, 360, 90, 0.45)]

DOCK_STEP = 68  # dock item height + gap; used to line flyouts up with their button


def _rgb(hex_):
    h = hex_.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


_wall = {"src": None, "surface": None, "offset": (0, 0), "size": None}


def _wallpaper():
    if _wall["src"] is None:
        from gi.repository import GdkPixbuf
        try:
            _wall["src"] = GdkPixbuf.Pixbuf.new_from_file(os.path.join(ui.SHARE, "wallpaper.jpg"))
        except GLib.Error:
            _wall["src"] = False
    return _wall["src"]


def draw_sky(widget, cr):
    """Wallpaper scaled to cover the screen; a drawn sky if the image is missing."""
    w, h = widget.get_allocated_width(), widget.get_allocated_height()
    src = _wallpaper()
    if src:
        from gi.repository import GdkPixbuf
        if _wall["size"] != (w, h):
            iw, ih = src.get_width(), src.get_height()
            s = max(w / iw, h / ih)
            pb = src.scale_simple(max(1, round(iw * s)), max(1, round(ih * s)),
                                  GdkPixbuf.InterpType.BILINEAR)
            # Convert to a cairo surface once; converting the pixbuf on every
            # redraw is expensive on slow thin clients.
            _wall["surface"] = Gdk.cairo_surface_create_from_pixbuf(pb, 1, None)
            _wall["offset"] = ((w - pb.get_width()) // 2, (h - pb.get_height()) // 2)
            _wall["size"] = (w, h)
        cr.set_source_surface(_wall["surface"], *_wall["offset"])
        cr.paint()
        return False
    import cairo
    g = cairo.LinearGradient(0, 0, 0, h)
    for off, col in SKY:
        g.add_color_stop_rgb(off, *_rgb(col))
    cr.set_source(g)
    cr.paint()
    for cx, cy, rx, ry, a in CLOUDS:
        cr.save()
        cr.translate(cx * w, cy * h)
        cr.scale(rx, ry)
        rg = cairo.RadialGradient(0, 0, 0, 0, 0, 1)
        rg.add_color_stop_rgba(0, 1, 1, 1, a)
        rg.add_color_stop_rgba(0.7, 1, 1, 1, 0)
        cr.set_source(rg)
        cr.arc(0, 0, 1, 0, 2 * math.pi)
        cr.fill()
        cr.restore()
    return False


class Shell(Gtk.Window):
    def __init__(self):
        super().__init__(title="LiteLink")
        ui.add_class(self, "litelink")
        self.set_decorated(False)
        self.cfg = Config()
        self.session = None
        self.kiosk = None         # KioskView while in kiosk mode
        self._creds = {}          # username / password typed for the current attempt
        self.layers = []          # stack of (container, kind, on_close)
        self.flyout = None
        self.flyout_name = None
        self._flyout_anchor = None

        self.root = Gtk.Overlay()
        self.add(self.root)

        sky = Gtk.DrawingArea()
        sky.connect("draw", draw_sky)
        self.root.add(sky)

        base = Gtk.Box()
        self.dock = self._build_dock()
        base.pack_start(self.dock, False, False, 0)
        self.desktop = Gtk.FlowBox(orientation=Gtk.Orientation.VERTICAL, selection_mode=Gtk.SelectionMode.NONE,
                                   homogeneous=True, column_spacing=8, row_spacing=8,
                                   valign=Gtk.Align.START, halign=Gtk.Align.START,
                                   margin_top=24, margin_start=20, max_children_per_line=6)
        base.pack_start(self.desktop, True, True, 0)
        self.root.add_overlay(base)

        self.clock = self._build_clock()
        self.root.add_overlay(self.clock)

        logo = ui.picture("logo.png", 112)
        logo.set_halign(Gtk.Align.END)
        logo.set_valign(Gtk.Align.END)
        logo.set_margin_end(28)
        logo.set_margin_bottom(24)
        logo.set_opacity(0.85)
        self.root.add_overlay(logo)

        self.refresh_desktop()
        self.connect("key-press-event", self._on_key)
        self.connect("destroy", Gtk.main_quit)
        GLib.timeout_add_seconds(1, self._tick_clock)

    # ================================================================ base UI
    def _build_dock(self):
        box = ui.add_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4), "dock")
        box.set_size_request(76, -1)
        self.dock_buttons = {}

        # The kite at the top opens the Settings menu (which also holds Add connection)
        home = Gtk.Button()
        ui.add_class(home, "dock-btn")
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, valign=Gtk.Align.CENTER)
        v.pack_start(ui.picture("icon.png", 36), False, False, 0)
        v.pack_start(Gtk.Label(label="Settings"), False, False, 0)
        home.add(v)
        home.connect("clicked", lambda *_: self.toggle_flyout("settings"))
        self.dock_buttons["settings"] = home
        box.pack_start(home, False, False, 0)
        box.pack_start(Gtk.Box(), True, True, 0)

        def item(name, label, cb):
            b = Gtk.Button()
            ui.add_class(b, "dock-btn")
            v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, valign=Gtk.Align.CENTER)
            v.pack_start(ui.icon(name, 22, "#3F434B"), False, False, 0)
            v.pack_start(Gtk.Label(label=label), False, False, 0)
            b.add(v)
            b.connect("clicked", lambda *_: cb())
            self.dock_buttons[name] = b
            box.pack_start(b, False, False, 0)

        item("network", "Network", lambda: self.toggle_flyout("network"))
        item("volume", "Volume", lambda: self.toggle_flyout("volume"))
        item("display", "Display", lambda: self.toggle_flyout("display"))
        box.pack_start(ui.add_class(Gtk.Box(), "dock-sep"), False, False, 0)
        item("power", "Power", self.show_power_menu)
        return box

    def _set_active(self, name):
        for n, b in self.dock_buttons.items():
            ctx = b.get_style_context()
            (ctx.add_class if n == name else ctx.remove_class)("active")

    def _build_clock(self):
        box = ui.add_class(Gtk.Box(spacing=10), "clock-pill")
        box.set_halign(Gtk.Align.END)
        box.set_valign(Gtk.Align.START)
        box.set_margin_top(20)
        box.set_margin_end(24)
        self.clock_date = Gtk.Label()
        self.clock_time = ui.label("", "strong")
        box.pack_start(self.clock_date, False, False, 0)
        box.pack_start(Gtk.Label(label="·"), False, False, 0)
        box.pack_start(self.clock_time, False, False, 0)
        self._tick_clock()
        return box

    def _tick_clock(self):
        from .config import fmt_date, fmt_time
        now = time.localtime()
        self.clock_date.set_text(fmt_date(self.cfg.data, now))
        self.clock_time.set_text(fmt_time(self.cfg.data, now))
        if getattr(self, "lock_clock", None):
            self.lock_clock.set_text(fmt_time(self.cfg.data, now))
        return True

    def refresh_desktop(self):
        for child in self.desktop.get_children():
            self.desktop.remove(child)
        # Only real connections live on the desktop (Add connection is in the
        # Settings menu). Until LiteLink is installed, an Install tile comes first.
        if not system.is_installed():
            self.desktop.add(self._install_icon())
        for conn in self.cfg.connections:
            self.desktop.add(self._desk_icon(conn))
        self.desktop.show_all()

    def _desk_icon(self, conn):
        b = ui.add_class(Gtk.Button(), "desk-icon")
        b.set_size_request(108, -1)
        b.set_tooltip_text(conn["host"])
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        ov = Gtk.Overlay(halign=Gtk.Align.CENTER)
        tile = ui.add_class(Gtk.Box(), "icon-tile")
        tile.set_size_request(60, 60)
        img = ui.icon("monitor", 30, "#2659B3", 1.7)
        img.set_hexpand(True)
        tile.pack_start(img, True, True, 0)
        ov.add(tile)
        badge = ui.label(conn.get("protocol", "rdp").upper(), "proto-badge")
        badge.set_halign(Gtk.Align.END)
        badge.set_valign(Gtk.Align.END)
        ov.add_overlay(badge)
        if conn.get("auto"):
            dot = ui.add_class(Gtk.Box(), "auto-dot")
            dot.set_halign(Gtk.Align.START)
            dot.set_valign(Gtk.Align.START)
            dot.set_tooltip_text("Connects at startup")
            ov.add_overlay(dot)
        v.pack_start(ov, False, False, 0)
        name = ui.label(conn["name"], "name", xalign=0.5, wrap=True)
        name.set_max_width_chars(12)
        v.pack_start(name, False, False, 0)
        b.add(v)
        b.connect("clicked", lambda *_: self.start_session(conn))
        b.connect("button-press-event", self._icon_menu, conn)
        child = Gtk.FlowBoxChild()
        child.add(b)
        return child

    def _install_icon(self):
        """Desktop tile shown only while running from a USB stick / DVD."""
        from .storage import Installer
        b = ui.add_class(Gtk.Button(), "desk-icon")
        b.set_size_request(108, -1)
        b.set_tooltip_text("Copy LiteLink to this device's internal storage")
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        tile = ui.add_class(Gtk.Box(halign=Gtk.Align.CENTER), "icon-tile", "app")
        tile.set_size_request(60, 60)
        tile.pack_start(ui.picture("icon.png", 60), True, True, 0)   # the LiteLink app icon is the tile
        v.pack_start(tile, False, False, 0)
        name = ui.label("Install LiteLink", "name", xalign=0.5, wrap=True)
        name.set_max_width_chars(12)
        v.pack_start(name, False, False, 0)
        b.add(v)
        b.connect("clicked", lambda *_: Installer(self).open())
        child = Gtk.FlowBoxChild()
        child.add(b)
        return child

    def _icon_menu(self, widget, event, conn):
        if event.button != 3:
            return False
        menu = Gtk.Menu()
        for text, cb in (("Connect", lambda *_: self.start_session(conn)),
                         ("Edit", lambda *_: self.edit_connection(conn)),
                         ("Delete", lambda *_: self._delete(conn))):
            mi = Gtk.MenuItem(label=text)
            mi.connect("activate", cb)
            menu.append(mi)
        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def _delete(self, conn):
        self.cfg.delete(conn["id"])
        self.refresh_desktop()

    # ================================================================ layers
    def push_layer(self, content, kind="dark", on_close=None, closable=True, center=True):
        """Show content above everything. kind: dark | light | lock | wizard | clear.
        lock and wizard are full screen over a fresh sky (wizard: soft tint for a light card)."""
        catcher = Gtk.EventBox()
        cls = {"dark": "scrim-dark", "light": "scrim-light", "lock": "lock-scrim"}.get(kind)
        if kind in ("lock", "wizard"):
            holder = Gtk.Overlay()
            sky = Gtk.DrawingArea()
            sky.connect("draw", draw_sky)
            holder.add(sky)
            tint = ui.add_class(Gtk.EventBox(), "lock-scrim" if kind == "lock" else "wizard-scrim")
            tint.add(content)
            holder.add_overlay(tint)
            catcher.add(holder)
        else:
            if cls:
                ui.add_class(catcher, cls)
            if center:
                content.set_halign(Gtk.Align.CENTER)
                content.set_valign(Gtk.Align.CENTER)
            catcher.add(content)
        entry = {"widget": catcher, "kind": kind, "on_close": on_close, "closable": closable}
        catcher.connect("button-press-event", self._layer_click, entry)
        self.layers.append(entry)
        self.root.add_overlay(catcher)
        catcher.show_all()
        return entry

    def _layer_click(self, widget, event, entry):
        # Right-click closes closable layers (e.g. the power menu).
        if event.button == 3 and entry["closable"] and entry is self.layers[-1:][0]:
            self.pop_layer(entry)
            return True
        return False

    def pop_layer(self, entry=None):
        if not self.layers:
            return
        entry = entry or self.layers[-1]
        if entry in self.layers:
            self.layers.remove(entry)
            self.root.remove(entry["widget"])
            entry["widget"].destroy()
            if entry["on_close"]:
                entry["on_close"]()

    def clear_layers(self):
        while self.layers:
            self.pop_layer()

    def _on_key(self, widget, event):
        key = Gdk.keyval_name(event.keyval)
        ctrl_alt = (event.state & Gdk.ModifierType.CONTROL_MASK) and (event.state & Gdk.ModifierType.MOD1_MASK)
        if ctrl_alt and key in ("t", "T") and system.have("xterm") and system.maintenance_enabled():
            subprocess.Popen(["xterm"])          # maintenance terminal
            return True
        if key == "Escape":
            if self.layers and self.layers[-1]["closable"]:
                self.pop_layer()
                return True
            if self.flyout:
                self.close_flyout()
                return True
        return False

    # ================================================================ flyouts
    def toggle_flyout(self, name, anchor=None, reopen=False):
        """Open (or close) a flyout next to its dock button. anchor=(left, bottom)
        places it elsewhere, e.g. above the kiosk screen's network pill; reopen
        rebuilds an open flyout in place (fresh status), keeping its anchor."""
        if reopen:
            if self.flyout_name != name:
                return
            anchor = self._flyout_anchor
        elif self.flyout_name == name:
            self.close_flyout()
            return
        self.close_flyout()
        from . import flyouts
        builder = getattr(flyouts, name)
        card, slot = builder(self)
        catcher = Gtk.EventBox()
        catcher.set_margin_start(76)
        if anchor is not None:
            catcher.set_margin_start(max(0, anchor[0] - 12))
            slot = anchor[1]
        self._flyout_anchor = anchor
        catcher.connect("button-press-event", lambda *_: (self.close_flyout(), True)[1])
        # slot >= 0: bottom margin of the dock button; < 0: anchored to the top
        top = slot < 0
        shield = Gtk.EventBox(halign=Gtk.Align.START, valign=Gtk.Align.START if top else Gtk.Align.END)
        shield.connect("button-press-event", lambda *_: True)   # clicks inside stay inside
        # Room around the card so its drop shadow isn't clipped to a hard edge.
        pad = 40
        card.set_margin_start(12)
        card.set_margin_end(pad)
        card.set_margin_top(pad)
        card.set_margin_bottom(pad)
        if top:
            card.set_margin_top(-slot)
        else:
            shield.set_margin_bottom(max(0, slot - pad))
        shield.add(card)
        catcher.add(shield)
        self.root.add_overlay(catcher)
        catcher.show_all()
        self.flyout, self.flyout_name = catcher, name
        self._set_active(name)

    def close_flyout(self):
        if self.flyout:
            self.root.remove(self.flyout)
            self.flyout.destroy()
        self.flyout = self.flyout_name = None
        self._set_active(None)

    # ================================================================ actions
    def edit_connection(self, conn, on_close=None):
        self.close_flyout()
        from .dialogs import ConnectionDialog
        dlg = ConnectionDialog(self, conn)
        self.push_layer(dlg, "light", on_close=on_close)

    def save_connection(self, conn):
        self.cfg.upsert(conn)
        self.refresh_desktop()
        if self.kiosk is not None:
            # e.g. the kiosk's first connection was just added: once its dialog
            # has closed, show (or connect with) it
            GLib.timeout_add(100, lambda: (self.kiosk is not None and not self.session and not self.layers
                                           and self.kiosk_home(), False)[1])

    def open_settings(self, panel="network", unlocked=False):
        """Settings. Behind the admin password when it's set and either the
        device is a kiosk (the kiosk screen asks) or Admin lock says so."""
        self.close_flyout()
        if (not unlocked and self.kiosk is None and self.cfg.data.get("settings_locked")
                and self.cfg.lock_needs_password()):
            from .dialogs import admin_password_dialog
            admin_password_dialog(self, lambda: self.open_settings(panel, unlocked=True))
            return
        from .dialogs import SettingsWindow
        # Closing Settings applies a mode change (Settings › System)
        self.push_layer(SettingsWindow(self, panel), "clear", on_close=self.apply_mode)

    # ================================================================ modes
    def apply_mode(self):
        """Show the desktop or the kiosk, whichever Settings › System says."""
        if self.cfg.mode == "kiosk":
            if self.kiosk is None:
                from .kiosk import KioskView
                self.close_flyout()
                self.kiosk = KioskView(self)
                self.root.add_overlay(self.kiosk)
                # Layers already open (e.g. Settings closing) stay above it
                for entry in self.layers:
                    self.root.reorder_overlay(entry["widget"], -1)
                self.kiosk.show_all()
                self.kiosk_home(startup=True)
            elif not self.layers:
                self.kiosk_home()
        elif self.kiosk is not None:
            self.root.remove(self.kiosk)
            self.kiosk.destroy()
            self.kiosk = None
            self.refresh_desktop()

    def kiosk_home(self, error=None, username=None, startup=False, autoconnect=True):
        """Back to the kiosk screen; connects by itself when the connection has
        a saved username and password."""
        if self.kiosk is None:
            return
        self.clear_layers()
        conn = self.cfg.kiosk_conn()
        self.kiosk.home(error, username)
        from .kiosk import should_autoconnect
        if autoconnect and not error and should_autoconnect(conn):
            GLib.timeout_add(600 if startup else 300, lambda: (
                self.kiosk is not None and not self.session and not self.layers and self.start_session(conn),
                False)[1])

    def show_power_menu(self):
        self.close_flyout()
        from .screens import power_menu
        self._set_active("power")
        self.push_layer(power_menu(self), "dark", on_close=lambda: self._set_active(None))

    def lock(self):
        self.clear_layers()
        from .screens import lock_screen
        self.lock_clock = None
        self.push_layer(lock_screen(self), "lock", closable=False, center=False)

    def unlock(self, entry):
        self.lock_clock = None
        self.pop_layer(entry)

    def start_session(self, conn, password=None, username=None, domain=None, ignore_cert=False):
        self.close_flyout()
        self.clear_layers()
        from . import screens
        from .rdp import Session
        if not conn["host"]:
            self.edit_connection(conn)
            return
        # No saved username or password: ask first (the kiosk asks on its own screen)
        if password is None and not has_credentials(conn):
            if self.kiosk is not None:
                self.kiosk_home(autoconnect=False)
            else:
                self.push_layer(screens.error(self, conn, "signin", 0, ""), "dark")
            return
        pw = password if password is not None else conn.get("password", "")
        self._creds = {k: v for k, v in (("password", password), ("username", username), ("domain", domain))
                       if v is not None}
        if self.kiosk is not None:
            self.kiosk.home()          # clear what was typed (and any old message) under the session
        layer = self.push_layer(screens.connecting(self, conn), "dark", closable=False)

        def launch():
            try:
                self.session = Session(conn, pw, lambda *a: self._session_ended(conn, layer, *a),
                                       username=username, domain=domain, ignore_cert=ignore_cert)
            except OSError as e:
                self.pop_layer(layer)
                self.push_layer(screens.error(self, conn, "unreachable", 255, str(e)), "dark")

        if not conn.get("vpn"):
            launch()
            return
        # Private network: bring the connection's WireGuard tunnel up first (if it isn't already)
        wait = self._vpn_wait = {"layer": layer, "cancelled": False}

        def bring_up():
            t = next((x for x in system.vpn_list() if x["uuid"] == conn["vpn"]), None)
            if t is None:
                return False, "The tunnel was removed. Choose another one in the connection's settings.", ""
            if t["active"]:
                return True, "", t["name"]
            ok, err = system.vpn_set(t["uuid"], True)
            return ok, err, t["name"]

        def ready(res):
            ok, err, name = res
            if wait["cancelled"] or self._vpn_wait is not wait:
                return
            self._vpn_wait = None
            if ok:
                launch()
            else:
                self.pop_layer(layer)
                self.push_layer(screens.error(self, conn, "vpn", 0, err,
                                              extra={"vpn_name": name or "VPN", "creds": self._creds}),
                                "dark", closable=self.kiosk is None)
        system.in_background(bring_up, ready)

    def cancel_session(self):
        if self.session:
            self.session.cancel()
        elif getattr(self, "_vpn_wait", None):        # still bringing the VPN up
            self._vpn_wait["cancelled"] = True
            self.pop_layer(self._vpn_wait["layer"])
            self._vpn_wait = None
            if self.kiosk is not None:
                self.kiosk_home(autoconnect=False)

    def _session_ended(self, conn, layer, kind, code, text, seconds):
        from . import screens
        cancelled = self.session is not None and self.session.cancelled
        self.session = None
        self.pop_layer(layer)
        creds, self._creds = self._creds, {}
        if self.kiosk is not None:
            if kind == "closed":
                # Signed out: ready for the next person. A session that closed at
                # once (cancelled, or the server refused it) doesn't loop. A web
                # session ended with End session starts again fresh.
                web_end = conn.get("protocol") in WEB_PROTOCOLS and cancelled
                self.kiosk_home(autoconnect=web_end or (not cancelled and seconds > 5))
                return False
            if kind == "auth":
                self.kiosk_home(error="The username or password wasn’t accepted.",
                                username=creds.get("username", conn.get("username", "")))
                return False
        elif kind == "closed":
            return False
        # A drop after a working session is "ended"; a fast failure is a connect error.
        if kind == "unreachable" and seconds > 20:
            kind = "ended"
        self.push_layer(screens.error(self, conn, kind, code, text, extra={"creds": creds}), "dark",
                        closable=kind != "ended" and self.kiosk is None)
        return False

    def autostart(self):
        self._restore_display()
        self._start_input()
        # Boot menu "Install LiteLink OS" -> open the installer straight away
        if system.boot_option("litelink.install"):
            from .storage import Installer
            GLib.timeout_add(800, lambda: (Installer(self).open(), False)[1])
            return
        if self.cfg.mode == "kiosk":
            self.apply_mode()          # the kiosk screen, connecting by itself if it can
            return
        auto = next((c for c in self.cfg.connections if c.get("auto")), None)
        if auto:
            GLib.timeout_add(600, lambda: (self.start_session(auto), False)[1])
        else:
            GLib.timeout_add(2500, lambda: (self._offer_usb_storage(), False)[1])

    # -- Keyboard & input: applied at startup and to devices plugged in later ----
    def _start_input(self):
        self._input_ids = None
        self._check_input()
        GLib.timeout_add_seconds(5, self._check_input)

    def _check_input(self):
        def work():
            ids = system.input_device_ids()
            if ids != self._input_ids:          # first run, or a mouse / keyboard came or went
                system.apply_input(self.cfg.data.get("input") or {})
            return ids

        def done(ids):
            self._input_ids = ids
        system.in_background(work, done)
        return True

    def _restore_display(self):
        """Re-apply a resolution the user kept (saved with persistent settings)."""
        saved = self.cfg.data.get("display_mode") or {}
        out, mode = saved.get("output"), saved.get("mode")
        if not out or not mode:
            return
        d = next((x for x in system.displays() if x["output"] == out), None)
        if d and mode in d["modes"] and mode != d["current"]:
            system.set_mode(out, mode)

    def _offer_usb_storage(self):
        """On a USB stick without settings storage, offer to set it up (once per boot)."""
        def show(st):
            if not st.get("can_setup_usb") or not st.get("medium_removable"):
                return
            card = ui.add_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10), "storage-card")
            card.set_halign(Gtk.Align.END)
            card.set_valign(Gtk.Align.START)
            card.set_margin_top(72)
            card.set_margin_end(24)
            card.set_size_request(320, -1)
            card.pack_start(ui.label("Settings aren't saved on this USB stick", "strong body"), False, False, 0)
            t = ui.label("Connections and Wi-Fi are lost when you restart. Set up storage to keep them.",
                         "muted small")
            t.set_line_wrap(True)
            t.set_max_width_chars(40)
            card.pack_start(t, False, False, 0)
            row = Gtk.Box(spacing=8, halign=Gtk.Align.END)

            def close():
                self.root.remove(card)
                card.destroy()

            def setup():
                close()
                from .storage import usb_setup
                usb_setup(self)
            row.pack_start(ui.button("Not now", "btn", close), False, False, 0)
            row.pack_start(ui.button("Set up", "btn btn-primary", setup), False, False, 0)
            card.pack_start(row, False, False, 0)
            self.root.add_overlay(card)
            card.show_all()
        system.in_background(system.storage_status, show)


def main():
    css = Gtk.CssProvider()
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    css.load_from_path(os.path.join(here, "theme.css"))
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css,
                                             Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    ui.install_pointer_cursors()
    win = Shell()
    screen = Gdk.Screen.get_default()
    win.set_default_size(screen.get_width(), screen.get_height())
    win.show_all()
    win.fullscreen()
    win.autostart()
    Gtk.main()
