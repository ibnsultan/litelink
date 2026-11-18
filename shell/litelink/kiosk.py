"""Kiosk mode: one connection, full screen, and nothing else.

There's no desktop and no dock. Between sessions the kiosk screen shows the
clock and, depending on the connection:
  - username and password saved: it connects by itself (at startup and after
    every sign-out), nothing to type;
  - one of them missing: a sign-in form, so each person uses their own account
    on the remote computer (a saved username is filled in);
  - no connection yet: "This kiosk isn't set up yet" with Add connection.
The corner holds Power, Settings (behind the admin password, entered in the same
place as the sign-in) and the network status.
"""
import time

from gi.repository import GLib, Gtk

from . import system, ui
from .config import WEB_PROTOCOLS, fmt_time, has_credentials

ERROR_MARKUP = '<span foreground="#FFB3B5">{}</span>'


class KioskView(Gtk.Overlay):
    def __init__(self, app):
        super().__init__()
        self.app = app
        ui.add_class(self, "kiosk")
        sky = Gtk.DrawingArea()
        from .app import draw_sky
        sky.connect("draw", draw_sky)
        self.add(sky)
        # Everything sits inside the tint (as on the lock screen): the tint has
        # its own window, which would otherwise be drawn over the content.
        tint = ui.add_class(Gtk.EventBox(), "lock-scrim")
        self.add_overlay(tint)
        self.inner = Gtk.Overlay()
        self.inner.add(Gtk.Box())
        tint.add(self.inner)

        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER,
                      valign=Gtk.Align.START, margin_top=120)
        self.clock = ui.label("", "lock-clock", xalign=0.5)
        self.date = ui.label("", "lock-date", xalign=0.5)
        top.pack_start(self.clock, False, False, 0)
        top.pack_start(self.date, False, False, 0)
        self.inner.add_overlay(top)

        # The middle swaps between sign-in, admin password and set-up
        self.stack = Gtk.Stack(halign=Gtk.Align.CENTER, valign=Gtk.Align.START, margin_top=420,
                               transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=120)
        self.stack.set_size_request(320, -1)
        self.inner.add_overlay(self.stack)

        self.inner.add_overlay(self._corner())
        logo = ui.picture("logo-light.png", 112)
        logo.set_halign(Gtk.Align.END)
        logo.set_valign(Gtk.Align.END)
        logo.set_margin_end(28)
        logo.set_margin_bottom(24)
        self.inner.add_overlay(logo)
        self.tick()
        self.timer = GLib.timeout_add_seconds(1, self.tick)
        self.connect("destroy", lambda *_: GLib.source_remove(self.timer))

    def tick(self):
        now = time.localtime()
        self.clock.set_text(fmt_time(self.app.cfg.data, now))
        self.date.set_text(time.strftime("%A, %-d %B", now))
        return True

    # ---------------------------------------------------------------- corner
    def _corner(self):
        corner = Gtk.Box(spacing=8, halign=Gtk.Align.START, valign=Gtk.Align.END, margin_start=28, margin_bottom=26)

        def round_btn(icon_name, tip, cb):
            b = ui.add_class(Gtk.Button(), "round-glass")
            b.add(ui.icon(icon_name, 18, "#FFFFFF", 1.9))
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda *_: cb())
            corner.pack_start(b, False, False, 0)
            return b
        round_btn("power", "Power options", self.app.show_power_menu)
        self.settings_btn = round_btn("settings", "Settings (admin)", self.ask_admin)
        # Network: live status (the network often comes up after the kiosk screen
        # appears); opens Settings › Network behind the admin password
        net = ui.add_class(Gtk.Button(), "round-glass")
        net.set_tooltip_text("Network")
        box = Gtk.Box(spacing=8)
        self.net_icon = ui.icon("network", 16, "#FFFFFF", 1.9)
        self.net_icon.set_margin_start(12)
        self.net_label = ui.label("Checking…", "small")
        self.net_label.set_margin_end(14)
        box.pack_start(self.net_icon, False, False, 0)
        box.pack_start(self.net_label, False, False, 0)
        net.add(box)
        # The same Network flyout as the dock's, above the pill (status, Wi-Fi to join);
        # changing the network from the kiosk asks for the admin password when one is set
        net.connect("clicked", lambda *_: self.app.toggle_flyout("network", anchor=(28, 26 + 40 + 8)))
        corner.pack_start(net, False, False, 0)
        self._net_busy = False
        self._poll_network()
        self.net_timer = GLib.timeout_add_seconds(5, self._poll_network)
        self.connect("destroy", lambda *_: GLib.source_remove(self.net_timer))
        return corner

    def _poll_network(self):
        if not self._net_busy:
            self._net_busy = True
            system.in_background(system.network_status, self._show_network)
        return True

    def _show_network(self, st):
        self._net_busy = False
        self.net_label.set_text(st["name"])
        name = {"ethernet": "ethernet", "wifi": "wifi3"}.get(st.get("kind"), "network")
        self.net_icon.set_from_pixbuf(ui.icon_pixbuf(name, 16, "#FFFFFF", 1.9))
        self.net_icon.set_opacity(1.0 if st.get("state") == "connected" else 0.6)

    # ---------------------------------------------------------------- pages
    def _show(self, name, widget):
        # Show the new page first and drop the old ones afterwards: this often
        # runs from a signal of a widget on the old page (Enter in the password
        # field), and destroying a widget inside its own handler crashes GTK.
        old = list(self.stack.get_children())
        self.stack.add(widget)
        widget.show_all()
        self.stack.set_visible_child(widget)

        def drop():
            for w in old:
                if w.get_parent() is self.stack:
                    self.stack.remove(w)
            return False
        GLib.idle_add(drop, priority=GLib.PRIORITY_DEFAULT)

    @staticmethod
    def _heading(title, sub):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, margin_bottom=4)
        box.pack_start(ui.label(title, "strong body", xalign=0.5), False, False, 0)
        box.pack_start(ui.label(sub, "kiosk-sub", xalign=0.5), False, False, 0)
        return box

    @staticmethod
    def _field(placeholder, text="", secret=False, sensitive=True):
        e = ui.add_class(Gtk.Entry(placeholder_text=placeholder, text=text or "", visibility=not secret),
                         "kiosk-field")
        e.set_sensitive(sensitive)
        return e

    def _with_go(self, entry, on_go, tip):
        """A password field with the round arrow button inside it (as on the lock screen)."""
        ov = Gtk.Overlay()
        ov.add(entry)
        go = ui.add_class(Gtk.Button(halign=Gtk.Align.END, valign=Gtk.Align.CENTER, margin_end=4), "round-blue")
        go.add(ui.icon("arrow", 18, "#FFFFFF", 2.2))
        go.set_tooltip_text(tip)
        go.set_can_focus(False)        # Tab goes Username → Password; Enter in the field signs in
        go.connect("clicked", lambda *_: on_go())
        ov.add_overlay(go)
        entry.connect("activate", lambda *_: on_go())
        return ov

    def home(self, error=None, username=None):
        """What the kiosk shows between sessions."""
        conn = self.app.cfg.kiosk_conn()
        if conn is None:
            self.show_setup()
        elif conn.get("protocol") in WEB_PROTOCOLS:
            self.show_start(conn)
        else:
            self.show_signin(conn, error, username)

    def show_start(self, conn):
        """Web sessions sign in on the page itself: just a way to start."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.pack_start(self._heading(conn["name"], "Web kiosk"),
                       False, False, 0)
        start = ui.button("Start", "btn pill btn-primary", lambda: self.app.start_session(conn))
        start.set_halign(Gtk.Align.CENTER)
        start.set_margin_top(6)
        box.pack_start(start, False, False, 0)
        self._show("start", box)

    def show_signin(self, conn, error=None, username=None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.pack_start(self._heading(conn["name"], "Sign in with your account"), False, False, 0)
        user = self._field("Username", username if username is not None else conn.get("username"))
        saved_pw = bool(conn.get("password"))
        pw = self._field("Saved password" if saved_pw else "Password", secret=True)

        def go():
            u = user.get_text().strip()
            p = pw.get_text() or (conn.get("password") if saved_pw else "")
            if not u or not p:
                (user if not u else pw).grab_focus()
                return
            self.app.start_session(conn, password=p, username=u)
        user.connect("activate", lambda *_: pw.grab_focus())
        box.pack_start(user, False, False, 0)
        box.pack_start(self._with_go(pw, go, "Sign in"), False, False, 0)
        if error:
            msg = ui.label("", "small", xalign=0.5)
            msg.set_markup(ERROR_MARKUP.format(GLib.markup_escape_text(error)))
            ui.add_class(msg, "kiosk-error")
            box.pack_start(msg, False, False, 0)
        self._show("signin", box)
        GLib.idle_add((pw if user.get_text() else user).grab_focus)

    def show_setup(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_size_request(480, -1)
        box.pack_start(ui.label("This kiosk isn’t set up yet", "strong body", xalign=0.5), False, False, 0)
        box.pack_start(ui.label("Set up the connection to get started.", "kiosk-sub", xalign=0.5), False, False, 0)
        add = ui.button("Add connection", "btn pill btn-primary", lambda: self._admin_then(self._add_connection))
        add.set_halign(Gtk.Align.CENTER)
        add.set_margin_top(10)
        box.pack_start(add, False, False, 0)
        self._show("setup", box)

    def _add_connection(self):
        from .dialogs import ConnectionDialog
        self.app.push_layer(ConnectionDialog(self.app, None), "light")

    # ---------------------------------------------------------------- admin
    def _admin_then(self, action):
        if self.app.cfg.lock_needs_password():
            self.ask_admin(action)
        else:
            action()

    def ask_admin(self, action=None, error=None):
        """Swap the sign-in for the admin password, in the same place and style."""
        action = action or (lambda: self.app.open_settings("mode"))
        if not self.app.cfg.lock_needs_password():
            action()
            return
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.pack_start(self._heading("Settings", "Enter the admin password"), False, False, 0)
        box.pack_start(self._field("", "Litelink", sensitive=False), False, False, 0)
        pw = self._field("Admin password", secret=True)

        def go():
            if self.app.cfg.check_lock_password(pw.get_text()):
                self.home()
                action()
            else:
                self.ask_admin(action, "That password isn’t right.")
        box.pack_start(self._with_go(pw, go, "Open Settings"), False, False, 0)
        if error:
            msg = ui.add_class(ui.label("", "small", xalign=0.5), "kiosk-error")
            msg.set_markup(ERROR_MARKUP.format(error))
            box.pack_start(msg, False, False, 0)
        cancel = ui.add_class(Gtk.Button(label="Cancel", halign=Gtk.Align.CENTER), "kiosk-link")
        cancel.connect("clicked", lambda *_: self.home())
        box.pack_start(cancel, False, False, 0)
        self._show("admin", box)
        GLib.idle_add(pw.grab_focus)


def should_autoconnect(conn):
    return conn is not None and has_credentials(conn)
