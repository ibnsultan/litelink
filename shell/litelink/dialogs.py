"""The connection dialog and the Settings window."""
import copy
import os
import shutil

from gi.repository import Gdk, GLib, Gtk

from . import system, ui, wifi
from .config import DEFAULT_CONNECTION, has_credentials, normalize

SETTINGS_GROUPS = [
    ("Connectivity", [("network", "Network", "network"), ("vpn", "VPN", "vpn"),
                      ("bluetooth", "Bluetooth", "bluetooth"), ("remote", "Connections", "remote")]),
    ("Device", [("display", "Display", "display"), ("keyboard", "Keyboard & input", "keyboard"),
                ("sound", "Sound", "sound"), ("time", "Time & region", "time")]),
    # Before installing this is where you install; afterwards it's settings storage
    ("System", [("mode", "System", "monitor"),
                ("storage", "Storage" if system.is_installed() else "Install", "storage"),
                ("diag", "Diagnostics", "diag"), ("about", "About", "system")]),
]

# Settings panels that exist today; the others are shown disabled until built.
READY_PANELS = {"network", "vpn", "bluetooth", "remote", "display", "keyboard", "sound", "time",
                "mode", "storage", "about", "diag"}

RESOLUTIONS = [("fullscreen", "Full screen (match monitor)"), ("1920x1080", "1920 × 1080"),
               ("1600x900", "1600 × 900"), ("1280x800", "1280 × 800"), ("1024x768", "1024 × 768")]
DEPTHS = [(32, "32-bit"), (24, "24-bit"), (16, "16-bit (low bandwidth)")]
DEVICES = [("clipboard", "Clipboard", "Copy and paste between here and the remote desktop"),
           ("audio", "Audio playback", "Play remote sound on this device"),
           ("microphone", "Microphone", "Use this device’s microphone remotely"),
           ("usb", "USB storage", "Show USB drives in the remote desktop"),
           ("smartcard", "Smart card", "Sign in with a smart card reader")]


def field(label_text, widget, optional=False):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    head = Gtk.Box(spacing=6)
    head.pack_start(ui.label(label_text, "field-label"), False, False, 0)
    if optional:
        head.pack_end(ui.label("optional", "optional"), False, False, 0)    # at the field's right edge
    box.pack_start(head, False, False, 0)
    box.pack_start(widget, False, False, 0)
    return box


def entry(text="", placeholder="", secret=False):
    e = Gtk.Entry(text=text or "", placeholder_text=placeholder)
    if secret:
        e.set_visibility(False)
    return e


def combo(options, current):
    """Select field (LiteLink dropdown, not GTK's combo box)."""
    return ui.Dropdown(options, current)


def small_dialog(title):
    """-> (outer card to push as a layer, inner content box). The padding
    lives on the inner box so it's inside the white card."""
    outer = ui.add_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), "dialog")
    outer.set_size_request(440, -1)
    dlg = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=24)
    outer.pack_start(dlg, True, True, 0)
    handle = Gtk.EventBox()          # drag by the title
    handle.add(ui.label(title, "h2"))
    dlg.pack_start(handle, False, False, 0)
    ui.make_draggable(outer, handle)
    return outer, dlg


def admin_password_dialog(app, on_ok):
    """Ask for the admin password (Settings locked in Admin lock)."""
    outer, dlg = small_dialog("Admin password")
    dlg.pack_start(ui.label("Enter the admin password to open Settings.", "muted body", wrap=True), False, False, 0)
    pw = entry("", "Password", secret=True)
    dlg.pack_start(pw, False, False, 0)
    status = ui.label("", "small")
    dlg.pack_start(status, False, False, 0)

    def ok(*_):
        if app.cfg.check_lock_password(pw.get_text()):
            app.pop_layer()
            on_ok()
        else:
            pw.set_text("")
            status.set_markup('<span foreground="#C53030">That password isn’t right.</span>')
    pw.connect("activate", ok)
    row = Gtk.Box(spacing=10, halign=Gtk.Align.END)
    row.pack_start(ui.button("Cancel", "btn", app.pop_layer), False, False, 0)
    row.pack_start(ui.button("Open Settings", "btn btn-primary", ok), False, False, 0)
    dlg.pack_start(row, False, False, 0)
    app.push_layer(outer, "light")
    GLib.idle_add(pw.grab_focus)


def set_admin_password_dialog(app, on_done=None):
    """Set, change or remove the admin password (Settings › Admin lock / System)."""
    has = app.cfg.lock_needs_password()
    outer, dlg = small_dialog("Change admin password" if has else "Set admin password")
    cur = entry("", "", secret=True)
    new = entry("", "", secret=True)
    again = entry("", "", secret=True)
    if has:
        dlg.pack_start(field("Current password", cur), False, False, 0)
    dlg.pack_start(field("New password", new), False, False, 0)
    dlg.pack_start(field("Confirm new password", again), False, False, 0)
    status = ui.label("", "small")
    dlg.pack_start(status, False, False, 0)

    def err(text):
        status.set_markup(f'<span foreground="#C53030">{text}</span>')

    def done():
        app.pop_layer()
        if on_done:
            on_done()

    def save(*_):
        if has and not app.cfg.check_lock_password(cur.get_text()):
            return err("The current password isn’t right.")
        if len(new.get_text()) < 4:
            return err("Use at least 4 characters.")
        if new.get_text() != again.get_text():
            return err("The passwords don’t match.")
        app.cfg.set_lock_password(new.get_text())
        done()

    def remove():
        if not app.cfg.check_lock_password(cur.get_text()):
            return err("Enter the current password to remove it.")
        app.cfg.set_lock_password("")
        app.cfg.data["settings_locked"] = False
        app.cfg.save()
        done()
    again.connect("activate", save)
    row = Gtk.Box(spacing=10)
    if has:
        row.pack_start(ui.button("Remove password", "btn", remove), False, False, 0)
    row.pack_end(ui.button("Save", "btn btn-primary", save), False, False, 0)
    row.pack_end(ui.button("Cancel", "btn", app.pop_layer), False, False, 0)
    dlg.pack_start(row, False, False, 0)
    app.push_layer(outer, "light")
    GLib.idle_add((cur if has else new).grab_focus)


def set_time_dialog(app, on_done=None):
    """Set the clock by hand (used by Settings and the installer)."""
    import time as _t
    outer, dlg = small_dialog("Set date & time")
    now = _t.localtime()
    date_e = entry(_t.strftime("%Y-%m-%d", now), "YYYY-MM-DD")
    time_e = entry(_t.strftime("%H:%M", now), "HH:MM")
    dlg.pack_start(field("Date", date_e), False, False, 0)
    dlg.pack_start(field("Time (24-hour)", time_e), False, False, 0)
    status = ui.label("", "small")
    dlg.pack_start(status, False, False, 0)

    def save():
        if system.set_time(f"{date_e.get_text().strip()} {time_e.get_text().strip()}:00"):
            app._tick_clock()
            app.pop_layer()
            if on_done:
                on_done()
        else:
            status.set_markup('<span foreground="#C53030">Use YYYY-MM-DD and HH:MM.</span>')
    row = Gtk.Box(spacing=10, halign=Gtk.Align.END)
    row.pack_start(ui.button("Cancel", "btn", app.pop_layer), False, False, 0)
    row.pack_start(ui.button("Save", "btn btn-primary", save), False, False, 0)
    dlg.pack_start(row, False, False, 0)
    app.push_layer(outer, "light")


def tz_picker(app, on_done=None):
    """Searchable time zone list (used by Settings and the installer).
    The zone is applied (and saved) right away."""
    outer, dlg = small_dialog("Time zone")
    outer.set_size_request(480, -1)
    search = Gtk.SearchEntry(placeholder_text="Search for a city or country")
    dlg.pack_start(search, False, False, 0)
    scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
    scroll.set_size_request(-1, 320)
    lb = ui.add_class(Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE), "tz-list")
    current = system.time_status()["timezone"]
    countries = system.zone_countries()
    zones = ["UTC"] + [f"{r}/{c}" for r, cs in sorted(system.timezones().items()) for c in cs]
    for z in zones:
        city = z.rsplit("/", 1)[-1].replace("_", " ")
        country = countries.get(z, "")
        row = Gtk.ListBoxRow()
        row.zone = z
        row.key = f"{z} {city} {country}".lower()
        box = Gtk.Box(spacing=10)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        col.pack_start(ui.label(city if z != "UTC" else "UTC", "body"), False, False, 0)
        col.pack_start(ui.label(country or z, "faint small"), False, False, 0)
        box.pack_start(col, True, True, 0)
        if z == current or (z == "UTC" and current in ("Etc/UTC", "UTC")):
            box.pack_start(ui.icon("check", 16, "#2F6FD6", 2.2), False, False, 0)
        row.add(box)
        lb.add(row)
    lb.set_filter_func(lambda row: search.get_text().lower() in row.key)
    search.connect("search-changed", lambda *_: lb.invalidate_filter())

    def picked(_lb, row):
        if system.set_timezone("Etc/UTC" if row.zone == "UTC" else row.zone):
            app._tick_clock()
        app.pop_layer()
        if on_done:
            on_done()
    lb.connect("row-activated", picked)
    scroll.add(lb)
    dlg.pack_start(scroll, True, True, 0)
    row = Gtk.Box(halign=Gtk.Align.END)
    row.pack_start(ui.button("Cancel", "btn", app.pop_layer), False, False, 0)
    dlg.pack_start(row, False, False, 0)
    app.push_layer(outer, "light")
    GLib.idle_add(search.grab_focus)


def tz_label(tz):
    """'EAT (Dar es Salaam, Tanzania)' style label for a zone."""
    import time as _t
    if tz in ("Etc/UTC", "UTC", "Etc/Universal", ""):
        return "UTC (Coordinated Universal Time)"
    city = tz.rsplit("/", 1)[-1].replace("_", " ")
    country = system.zone_countries().get(tz, "")
    abbr = _t.strftime("%Z")
    place = f"{city}, {country}" if country else city
    return f"{abbr} ({place})" if abbr and not abbr.startswith(("+", "-")) else place


class Tabs(Gtk.Box):
    """Underlined text tabs driving a Gtk.Stack."""

    def __init__(self, stack, names):
        super().__init__(spacing=24)
        ui.add_class(self, "tabs")
        self.stack, self.buttons = stack, {}
        for key, text in names:
            b = ui.add_class(Gtk.Button(label=text), "tab")
            b.connect("clicked", lambda _b, k=key: self.select(k))
            self.buttons[key] = b
            self.pack_start(b, False, False, 0)
        # The stack pages are added after the tabs; select the first once they exist.
        GLib.idle_add(lambda: self.select(names[0][0]) and False)

    def select(self, key):
        self.stack.set_visible_child_name(key)
        for k, b in self.buttons.items():
            ctx = b.get_style_context()
            (ctx.add_class if k == key else ctx.remove_class)("active")


class ConnectionDialog(Gtk.Box):
    def __init__(self, app, conn):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        ui.add_class(self, "dialog")
        self.set_size_request(620, -1)
        self.app = app
        self.conn = copy.deepcopy(conn) if conn else normalize(copy.deepcopy(DEFAULT_CONNECTION))
        self.is_new = conn is None
        c = self.conn

        head = Gtk.Box(spacing=12, margin_top=18, margin_start=20, margin_end=14)
        head.pack_start(ui.picture("icon.png", 28), False, False, 0)
        head.pack_start(ui.label("New connection" if self.is_new else "Edit connection", "h2"), True, True, 0)
        close = ui.add_class(Gtk.Button(), "icon-btn")
        close.add(ui.icon("close", 18, "#5A606B", 2))
        close.connect("clicked", lambda *_: app.pop_layer())
        head.pack_start(close, False, False, 0)
        handle = Gtk.EventBox()          # drag the dialog by its header
        handle.add(head)
        self.pack_start(handle, False, False, 0)
        ui.make_draggable(self, handle)

        stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        tabs = Tabs(stack, [("general", "General"), ("display", "Display"), ("devices", "Devices")])
        tabs.set_margin_start(20)
        tabs.set_margin_end(20)
        tabs.set_margin_top(14)

        # -- General
        g = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin=20)
        proto = Gtk.Box(spacing=8)
        rdp = ui.button("Remote Desktop (RDP)", "btn proto-on")
        proto.pack_start(rdp, False, False, 0)
        for name in ("VNC", "Web kiosk"):
            b = ui.button(name, "btn proto-off")
            b.set_sensitive(False)
            b.set_tooltip_text("Not supported yet")
            proto.pack_start(b, False, False, 0)
        g.pack_start(field("Protocol", proto), False, False, 0)

        self.e_name = entry(c["name"] if not self.is_new else "", "e.g. Office Desktop")
        self.e_host = entry(c["host"], "Host name or IP address")
        self.e_user = entry(c["username"], "e.g. jdoe")
        self.e_pass = entry(c["password"], "Ask when connecting", secret=True)
        self.e_domain = entry(c["domain"], "CORP")
        self.e_gw = entry(c["gateway"], "gateway.corp.com")
        grid = Gtk.Grid(column_spacing=16, row_spacing=14, column_homogeneous=True)
        grid.attach(field("Name", self.e_name), 0, 0, 2, 1)
        grid.attach(field("Host or IP", self.e_host), 0, 1, 2, 1)
        grid.attach(field("Username", self.e_user), 0, 2, 1, 1)
        grid.attach(field("Password", self.e_pass), 1, 2, 1, 1)
        grid.attach(field("Domain", self.e_domain, optional=True), 0, 3, 1, 1)
        grid.attach(field("RD Gateway", self.e_gw, optional=True), 1, 3, 1, 1)
        # Private network: a WireGuard tunnel from Settings > VPN, brought up first
        tunnels = system.vpn_list()
        opts = [("", "None")] + [(t["uuid"], t["name"]) for t in tunnels]
        current = c.get("vpn", "") if any(t["uuid"] == c.get("vpn") for t in tunnels) else ""
        self.cb_vpn = combo(opts, current)
        vpn_box = field("VPN", self.cb_vpn, optional=True)
        vpn_box.pack_start(ui.label("Connects first when this connection opens" if tunnels else
                                    "Add tunnels in Settings › VPN", "muted small"), False, False, 0)
        if not tunnels:
            self.cb_vpn.set_sensitive(False)
        grid.attach(vpn_box, 0, 4, 2, 1)
        g.pack_start(grid, False, False, 0)
        self.c_auto = Gtk.CheckButton(label="Connect automatically at startup", active=c["auto"])
        self.c_reconnect = Gtk.CheckButton(label="Reconnect when the session ends", active=c["reconnect"])
        g.pack_start(self.c_auto, False, False, 0)
        g.pack_start(self.c_reconnect, False, False, 0)
        stack.add_named(g, "general")

        # -- Display
        d = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16, margin=20)
        self.cb_res = combo(RESOLUTIONS, c["display"]["resolution"])
        self.cb_depth = combo(DEPTHS, c["display"]["depth"])
        d.pack_start(field("Resolution", self.cb_res), False, False, 0)
        d.pack_start(field("Colour depth", self.cb_depth), False, False, 0)
        self.c_multi = Gtk.CheckButton(label="Use all monitors", active=c["display"]["multimon"])
        self.c_dyn = Gtk.CheckButton(label="Resize the remote desktop with the window", active=c["display"]["dynamic"])
        d.pack_start(self.c_multi, False, False, 0)
        d.pack_start(self.c_dyn, False, False, 0)
        stack.add_named(d, "display")

        # -- Devices
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin=20)
        intro = ui.label("Choose what this device shares with the remote desktop.", "muted small")
        intro.set_margin_bottom(6)
        v.pack_start(intro, False, False, 0)
        self.dev_checks = {}
        # A plain list: rows separated by a bottom border, none after the last
        for i, (key, title, hint) in enumerate(DEVICES):
            row = ui.add_class(Gtk.Box(spacing=12), "device-row",
                               *(["device-row-sep"] if i < len(DEVICES) - 1 else []))
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.pack_start(ui.label(title, "body strong"), False, False, 0)
            col.pack_start(ui.label(hint, "muted small"), False, False, 0)
            row.pack_start(col, True, True, 0)
            chk = Gtk.CheckButton(active=c["devices"].get(key, False), valign=Gtk.Align.CENTER)
            self.dev_checks[key] = chk
            row.pack_start(chk, False, False, 0)
            v.pack_start(row, False, False, 0)
        stack.add_named(v, "devices")

        self.pack_start(tabs, False, False, 0)
        self.pack_start(stack, True, True, 0)

        foot = ui.add_class(Gtk.Box(spacing=10), "dialog-footer")
        self.err = ui.label("", "small")
        foot.pack_start(self.err, True, True, 0)
        foot.pack_start(ui.button("Cancel", "btn", app.pop_layer), False, False, 0)
        foot.pack_start(ui.button("Save", "btn btn-primary", self.save), False, False, 0)
        self.pack_start(foot, False, False, 0)

    def save(self):
        host = self.e_host.get_text().strip()
        if not host:
            self.err.set_markup('<span foreground="#C53030">Enter a host name or IP address.</span>')
            return
        c = self.conn
        c.update({
            "name": self.e_name.get_text().strip() or host,
            "host": host,
            "username": self.e_user.get_text().strip(),
            "password": self.e_pass.get_text(),
            "domain": self.e_domain.get_text().strip(),
            "gateway": self.e_gw.get_text().strip(),
            "vpn": self.cb_vpn.get_active_id() or "",
            "auto": self.c_auto.get_active(),
            "reconnect": self.c_reconnect.get_active(),
        })
        depth = self.cb_depth.get_active_id()
        c["display"] = {"resolution": self.cb_res.get_active_id() or "fullscreen",
                        "depth": int(depth) if depth else 32,
                        "multimon": self.c_multi.get_active(), "dynamic": self.c_dyn.get_active()}
        c["devices"] = {k: chk.get_active() for k, chk in self.dev_checks.items()}
        if c["auto"]:   # only one connection starts automatically
            for other in self.app.cfg.connections:
                if other["id"] != c["id"]:
                    other["auto"] = False
        self.app.save_connection(c)
        self.app.pop_layer()


class SettingsWindow(Gtk.Box):
    def __init__(self, app, panel="network"):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        ui.add_class(self, "dialog")
        self.set_size_request(960, 616)
        self.app = app

        head = Gtk.Box(spacing=10, margin_start=18, margin_end=12)
        head.set_size_request(-1, 52)
        head.pack_start(ui.picture("icon.png", 24), False, False, 0)
        head.pack_start(ui.label("Settings", "strong body"), True, True, 0)
        self.max_btn = ui.add_class(Gtk.Button(valign=Gtk.Align.CENTER), "icon-btn")
        self.max_icon = ui.icon("maximize", 16, "#5A606B", 2)
        self.max_btn.add(self.max_icon)
        self.max_btn.set_tooltip_text("Maximize")
        self.max_btn.connect("clicked", lambda *_: self.toggle_maximize())
        head.pack_start(self.max_btn, False, False, 0)
        close = ui.add_class(Gtk.Button(valign=Gtk.Align.CENTER), "icon-btn")
        close.add(ui.icon("close", 18, "#5A606B", 2))
        close.connect("clicked", lambda *_: app.pop_layer())
        head.pack_start(close, False, False, 0)
        handle = Gtk.EventBox()          # drag by the header; double-click maximizes
        handle.add(head)
        self.pack_start(handle, False, False, 0)
        self.maximized = False
        ui.make_draggable(self, handle, on_double=self.toggle_maximize)
        self.pack_start(Gtk.Separator(), False, False, 0)

        body = Gtk.Box()
        nav = ui.add_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2), "settings-nav")
        nav.set_size_request(236, -1)
        self.nav_buttons = {}
        for title, items in SETTINGS_GROUPS:
            g = ui.label(title.upper(), "group-title")
            g.set_margin_start(10)
            g.set_margin_top(12)
            g.set_margin_bottom(6)
            nav.pack_start(g, False, False, 0)
            for key, text, icon_name in items:
                b = ui.add_class(Gtk.Button(), "nav-btn")
                row = Gtk.Box(spacing=10)
                row.pack_start(ui.icon(icon_name, 18, "#2B2E34"), False, False, 0)
                row.pack_start(ui.label(text), False, False, 0)
                b.add(row)
                b.connect("clicked", lambda _b, k=key: self.show(k))
                if key not in READY_PANELS:
                    b.set_sensitive(False)
                    b.set_tooltip_text("Coming in a later build")
                self.nav_buttons[key] = b
                nav.pack_start(b, False, False, 0)
        body.pack_start(nav, False, False, 0)

        self.content = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        body.pack_start(self.content, True, True, 0)
        self.pack_start(body, True, True, 0)
        self.show(panel if panel in READY_PANELS else "network")

    def toggle_maximize(self):
        """Fill the screen beside the dock, or go back to the centred window."""
        self.maximized = not self.maximized
        if self.maximized:
            self.set_size_request(-1, -1)
            self.set_halign(Gtk.Align.FILL)
            self.set_valign(Gtk.Align.FILL)
            self.set_margin_start(76 + 16)      # keep the dock visible
            self.set_margin_top(16)
            self.set_margin_end(16)
            self.set_margin_bottom(16)
        else:
            self.set_size_request(960, 616)
            self.set_halign(Gtk.Align.CENTER)
            self.set_valign(Gtk.Align.CENTER)
            for m in (self.set_margin_start, self.set_margin_top, self.set_margin_end, self.set_margin_bottom):
                m(0)
        self.max_icon.set_from_pixbuf(ui.icon_pixbuf("restore" if self.maximized else "maximize", 16, "#5A606B", 2))
        self.max_btn.set_tooltip_text("Restore" if self.maximized else "Maximize")

    def show(self, key):
        for k, b in self.nav_buttons.items():
            ctx = b.get_style_context()
            (ctx.add_class if k == key else ctx.remove_class)("active")
        child = self.content.get_child()
        if child:
            self.content.remove(child)
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24, margin_bottom=24,
                       margin_start=28, margin_end=28)
        titles = {k: t for _g, items in SETTINGS_GROUPS for k, t, _i in items}
        # Page header: title (+ description underneath) on the left, the page's
        # action on the title's line at the right (see set_header)
        self.head = Gtk.Grid(column_spacing=24, row_spacing=6)
        title = ui.label(titles.get(key, ""), "h3")
        title.set_hexpand(True)
        title.set_valign(Gtk.Align.CENTER)
        self.head.attach(title, 0, 0, 1, 1)
        page.pack_start(self.head, False, False, 0)
        builder = getattr(self, f"_panel_{key}", None)
        if builder:
            builder(page)
        else:
            ph = ui.add_class(Gtk.Box(), "placeholder")
            ph.set_size_request(-1, 360)
            ph.pack_start(ui.label("Coming in a later build.", "muted body", xalign=0.5), True, True, 0)
            page.pack_start(ph, True, True, 0)
        self.content.add(page)
        self.content.show_all()

    def set_header(self, desc=None, action=None):
        """Panel header extras: a description under the title (it ends where the
        action starts) and an action button on the title's line."""
        if action is not None:
            action.set_valign(Gtk.Align.CENTER)
            self.head.attach(action, 1, 0, 1, 1)
        if desc:
            d = ui.label(desc, "muted body", wrap=True)
            d.set_max_width_chars(80)
            self.head.attach(d, 0, 1, 1, 1)

    # -- panels -------------------------------------------------------------
    def _kv(self, rows, key_width=160):
        grid = Gtk.Grid(column_spacing=16, row_spacing=10)
        for i, (k, v) in enumerate(rows):
            kl = ui.label(k, "muted body")
            kl.set_size_request(key_width, -1)
            grid.attach(kl, 0, i, 1, 1)
            grid.attach(ui.label(v, "body"), 1, i, 1, 1)
        return grid

    # -- Network: one expandable card per adapter (Windows-style) ------------
    def _acc_card(self, icon_name, title, status, status_cls, expandable=True, extra=None, open_=False):
        """An adapter card: header row (icon, title, status, extra, chevron) and a
        revealer body. Returns (card, body box)."""
        card = ui.add_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), "acc")
        # The header is a click area *around* its contents, not a Gtk.Button:
        # a button swallows clicks meant for controls inside it (the `extra`
        # switch would only toggle the card). Here the switch gets its own
        # clicks and everything else opens / closes the card.
        head = ui.add_class(Gtk.EventBox(), "acc-head", *(["clickable"] if expandable else []))
        head.set_can_focus(expandable)
        # Enter / leave: hover highlight and the pointer cursor (ui.install_pointer_cursors)
        head.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        row = Gtk.Box(spacing=14, margin_top=14, margin_bottom=14, margin_start=16, margin_end=16)
        chip = ui.add_class(Gtk.Box(), "ico-chip")
        chip.pack_start(ui.icon(icon_name, 20, "#2659B3" if expandable else "#8A8F98"), True, True, 0)
        row.pack_start(chip, False, False, 0)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER)
        col.pack_start(ui.label(title, "strong body"), False, False, 0)
        col.pack_start(ui.label(status, status_cls), False, False, 0)
        row.pack_start(col, True, True, 0)
        if extra:
            row.pack_start(extra, False, False, 0)
        chev = ui.icon("chev_up" if open_ else "chev_down", 18, "#5A606B", 2)
        row.pack_start(chev, False, False, 0)
        head.add(row)
        card.pack_start(head, False, False, 0)
        rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, reveal_child=open_)
        body = ui.add_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), "acc-body")
        rev.add(body)
        card.pack_start(rev, False, False, 0)

        def flip(*_):
            rev.set_reveal_child(not rev.get_reveal_child())
            chev.set_from_pixbuf(ui.icon_pixbuf("chev_up" if rev.get_reveal_child() else "chev_down",
                                                18, "#5A606B", 2))

        def released(_w, ev):
            if ev.button == 1 and expandable:
                flip()
            return False

        def key(_w, ev):
            if expandable and Gdk.keyval_name(ev.keyval) in ("Return", "KP_Enter", "space"):
                flip()
                return True
            return False

        def hover(_w, ev, on):
            if ev.detail == Gdk.NotifyType.INFERIOR:      # moving onto the switch isn't leaving
                return False
            if on:
                head.set_state_flags(Gtk.StateFlags.PRELIGHT, False)
            else:
                head.unset_state_flags(Gtk.StateFlags.PRELIGHT)
            return False
        head.connect("button-release-event", released)
        head.connect("key-press-event", key)
        head.connect("enter-notify-event", hover, True)
        head.connect("leave-notify-event", hover, False)
        if not expandable:
            ui.add_class(head, "disabled")
            ui.add_class(card, "disabled")
            for w in (chip, col, chev):
                w.set_sensitive(False)
        return card, body

    def _acc_row(self, body, label_text, value, action=None, on_action=None, enabled=True):
        row = ui.add_class(Gtk.Box(spacing=12), "acc-row")
        k = ui.label(label_text, "body" if enabled else "faint body")
        k.set_size_request(190, -1)
        row.pack_start(k, False, False, 0)
        v = ui.label(value, "muted body")
        v.set_selectable(True)
        v.set_line_wrap(True)
        v.set_max_width_chars(44)   # long values wrap instead of widening the window
        row.pack_start(v, True, True, 0)
        if action:
            b = ui.button(action, "btn", on_action)
            b.set_sensitive(enabled)
            if not enabled:
                b.set_tooltip_text("Not supported yet")
            row.pack_start(b, False, False, 0)
        body.pack_start(row, False, False, 0)
        return row

    def _panel_network(self, page):
        adapters = system.adapters()
        wired = [a for a in adapters if a["type"] == "ethernet"]
        wireless = [a for a in adapters if a["type"] == "wifi"]

        if not wired:
            page.pack_start(self._acc_card("ethernet", "Ethernet", "No Ethernet adapter found",
                                           "muted small", expandable=False)[0], False, False, 0)
        for i, a in enumerate(wired):
            up = a["state"] == "connected"
            status = ("Connected" + (f" · {a['speed']}" if a["speed"] else "")) if up else "Not connected"
            title = "Ethernet" if len(wired) == 1 else f"Ethernet {i + 1}"
            card, body = self._acc_card("ethernet", title, status, "ok small" if up else "muted small",
                                        open_=(i == 0))
            if a["connection"]:
                d = system.dot1x_get(a["connection"])
                eap = {"peap": "PEAP", "ttls": "TTLS", "tls": "TLS (certificate)"}.get(d["eap"], "")
                val = f"{eap} · {d['identity']}" if eap and d["identity"] else (eap or "Off")
            else:
                d, val = None, "Off"
            self._acc_row(body, "802.1X authentication", val, "Edit",
                          lambda a=a, d=d: self._edit_dot1x(a, d), enabled=bool(a["connection"]))
            if up:
                gw = f" · gateway {a['gateway']}" if a["gateway"] else ""
                self._acc_row(body, "IPv4 address", (a["address"] or "—") + gw)
            hw = ui.add_class(Gtk.Box(spacing=12), "acc-row")
            grid = Gtk.Grid(column_spacing=12, row_spacing=8)
            rows = [("Manufacturer", a["vendor"] or "Unknown"), ("Description", a["product"] or a["device"]),
                    ("Link speed", (a["speed"] + (f", {a['duplex']} duplex" if a["duplex"] else "")) or "—"),
                    ("MAC address", a["mac"] or "—")]
            for r, (k, v) in enumerate(rows):
                kl = ui.label(k, "body")
                kl.set_size_request(190, -1)
                grid.attach(kl, 0, r, 1, 1)
                vl = ui.label(v, "muted body")
                vl.set_selectable(True)
                vl.set_line_wrap(True)
                vl.set_max_width_chars(44)
                grid.attach(vl, 1, r, 1, 1)
            hw.pack_start(grid, True, True, 0)
            text = "\n".join(f"{k}: {v}" for k, v in rows)
            copy = ui.button("Copy", "btn", lambda t=text: self._copy(t))
            copy.set_valign(Gtk.Align.START)
            hw.pack_start(copy, False, False, 0)
            body.pack_start(hw, False, False, 0)
            page.pack_start(card, False, False, 0)

        # Wi-Fi: greyed out and switched off when there is no Wi-Fi hardware
        if not wireless:
            sw = Gtk.Switch(active=False, valign=Gtk.Align.CENTER)
            sw.set_sensitive(False)
            extra = Gtk.Box(spacing=8)
            extra.pack_start(ui.label("Off", "muted small"), False, False, 0)
            extra.pack_start(sw, False, False, 0)
            page.pack_start(self._acc_card("network", "Wi-Fi", "No Wi-Fi adapter found", "muted small",
                                           expandable=False, extra=extra)[0], False, False, 0)
            self._net_general(page, adapters)
            return
        on = system.wifi_enabled()
        sw = Gtk.Switch(active=on, valign=Gtk.Align.CENTER)
        a = wireless[0]
        status = (f"Connected to {a['connection']}" if a["state"] == "connected"
                  else "On · not connected" if on else "Off")
        card, body = self._acc_card("network", "Wi-Fi", status, "ok small" if a["state"] == "connected"
                                    else "muted small", extra=sw, open_=on)
        page.pack_start(card, False, False, 0)

        def fill():
            for ch in body.get_children():
                body.remove(ch)
            if not sw.get_active():
                self._acc_row(body, "Wi-Fi is off", "")
            else:
                found = system.wifi_list()
                if not found:
                    self._acc_row(body, "No networks found", "")
                saved = system.wifi_saved()
                group = wifi.Group()
                for n in found[:8]:             # the connected network comes first
                    if n["active"]:
                        row = ui.add_class(Gtk.Box(spacing=10), "acc-row")
                        row.pack_start(ui.icon(wifi.signal_icon(n["signal"]), 16, "#16171A"), False, False, 0)
                        row.pack_start(ui.label(n["ssid"], "body"), True, True, 0)
                        row.pack_start(ui.label("Connected", "ok small"), False, False, 0)
                    else:
                        # Opens in place: password, Connect automatically, progress
                        row = ui.add_class(wifi.NetworkRow(n, group, lambda: self.show("network"),
                                                           saved=n["ssid"] in saved, icon_color="#16171A",
                                                           form_width=320),
                                           "acc-row")
                    body.pack_start(row, False, False, 0)
            body.show_all()
            return False

        def toggled(s, *_):
            system.wifi_set(s.get_active())
            GLib.timeout_add(1500, fill)
        sw.connect("notify::active", toggled)
        GLib.idle_add(fill)
        self._net_general(page, adapters)

    def _net_general(self, page, adapters):
        """IP and DNS: settings of the device's network as a whole, not of one
        adapter. IP assignment belongs to the connection in use; DNS servers
        apply to every adapter and network."""
        inuse = next((a for a in adapters if a["state"] == "connected" and a["connection"]), None)
        any_profile = inuse or next((a for a in adapters if a["connection"]), None)
        head = ui.label("IP & DNS", "group-title")
        head.set_margin_top(6)
        page.pack_start(head, False, False, 0)
        g = self._list_group(page)
        if inuse:
            where = "Ethernet" if inuse["type"] == "ethernet" else inuse["connection"]
            ip_sub = f"For the connection in use ({where})"
            ip_val = "Automatic (DHCP)" if inuse["method"] == "auto" else "Manual"
            if inuse["address"]:
                ip_val += f" · {inuse['address']}"
        else:
            ip_sub, ip_val = "Connect to a network first", "—"
        self._list_row(g, "IP assignment", ip_sub, self._value(ip_val),
                       on_click=(lambda: self._edit_ip(inuse)) if inuse else None, enabled=bool(inuse))
        dns_manual = bool(any_profile and any_profile["dns_manual"])
        if dns_manual:
            dns_val = ", ".join(any_profile["dns"]) or "Manual"
        elif inuse and inuse["dns"]:
            dns_val = "Automatic · " + ", ".join(inuse["dns"])
        else:
            dns_val = "Automatic (DHCP)"
        self._list_row(g, "DNS servers", "Used by every adapter and network", self._value(dns_val),
                       on_click=(lambda: self._edit_dns(any_profile)) if any_profile else None,
                       enabled=bool(any_profile))

    # -- Time & region --------------------------------------------------------
    def _list_group(self, page):
        """A rounded group of list rows (GNOME-style settings list)."""
        group = ui.add_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), "acc", "list-group")
        page.pack_start(group, False, False, 0)
        return group

    def _list_row(self, group, title, subtitle=None, end=None, on_click=None, enabled=True):
        """Title (+ grey subtitle) on the left, control or value on the right.
        Rows with on_click are buttons and show a chevron."""
        inner = Gtk.Box(spacing=12)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        col.pack_start(ui.label(title, "body"), False, False, 0)
        if subtitle:
            sub = ui.label(subtitle, "faint small", wrap=True)
            sub.set_max_width_chars(70)
            col.pack_start(sub, False, False, 0)
        inner.pack_start(col, True, True, 0)
        if end is not None:
            inner.pack_start(end, False, False, 0)
        if on_click:
            inner.pack_start(ui.icon("chev_right", 16, "#8A8F98", 2), False, False, 0)
            row = ui.add_class(Gtk.Button(), "list-row", "list-row-btn")
            row.add(inner)
            row.connect("clicked", lambda *_: on_click())
        else:
            row = ui.add_class(Gtk.Box(), "list-row")
            row.pack_start(inner, True, True, 0)
        if group.get_children():
            ui.add_class(row, "list-row-sep")
        row.set_sensitive(enabled)
        group.pack_start(row, False, False, 0)
        return row

    @staticmethod
    def _value(text):
        v = ui.label(text, "muted body")
        v.set_ellipsize(3)
        v.set_max_width_chars(40)
        return v

    def _tz_label(self, tz):
        return tz_label(tz)

    def _panel_time(self, page):
        from .config import DATE_FORMATS
        import time as _t
        st = system.time_status()
        cfg = self.app.cfg

        # -- Date, time and zone -------------------------------------------
        g = self._list_group(page)
        auto = Gtk.Switch(active=st["ntp"], valign=Gtk.Align.CENTER)
        if st["ntp"] and st["synced"] and st["server"]:
            sub = f"Synchronized with {st['server']}"
        elif st["ntp"]:
            sub = "Uses internet time servers · waiting for a server"
        else:
            sub = "Uses internet time servers"
        self._list_row(g, "Automatic date & time", sub, auto)

        tz_on = bool(cfg.data.get("auto_timezone"))
        auto_tz = Gtk.Switch(active=tz_on, valign=Gtk.Align.CENTER)
        failed = getattr(self.app, "auto_tz_failed", False)
        self._list_row(g, "Automatic time zone",
                       "Couldn’t find it: no internet connection? It’s tried again at startup." if tz_on and failed
                       else "Found from this device’s internet address (ipapi.co) at startup", auto_tz)

        def tz_toggled(s, *_):
            cfg.data["auto_timezone"] = s.get_active()
            cfg.save()
            if s.get_active():
                s.set_sensitive(False)
                system.in_background(self.app.auto_timezone_now, lambda _tz: self.show("time"))
            else:
                self.show("time")
        auto_tz.connect("notify::active", tz_toggled)

        now = _t.localtime()
        from .config import fmt_time
        stamp = f"{_t.strftime('%-d %b %Y', now)}, {fmt_time(cfg.data, now)}"
        self._list_row(g, "Date & time", None, self._value(stamp), on_click=self._set_time_dialog,
                       enabled=not st["ntp"])
        self._list_row(g, "Time zone", "Set automatically" if tz_on else None,
                       self._value(self._tz_label(st["timezone"])), on_click=self._tz_picker, enabled=not tz_on)
        servers = system.ntp_servers()
        self._list_row(g, "Time servers", "Use your organisation's servers if public ones are blocked",
                       self._value(", ".join(servers) if servers else "Automatic"),
                       on_click=self._ntp_dialog)

        def auto_toggled(s, *_):
            system.set_ntp(s.get_active())
            GLib.timeout_add(1500, lambda: (self.show("time"), False)[1])
        auto.connect("notify::active", auto_toggled)

        # -- Formats -------------------------------------------------------
        g2 = self._list_group(page)
        clock = combo([("24", "24-hour"), ("12", "12-hour")], "24" if cfg.data.get("clock_24h", True) else "12")
        datef = combo([(k, ex) for k, _p, ex in DATE_FORMATS], cfg.data.get("date_format", "short"))
        for w in (clock, datef):
            w.set_size_request(180, -1)

        def save_formats(*_):
            cfg.data["clock_24h"] = clock.get_active_id() == "24"
            cfg.data["date_format"] = datef.get_active_id()
            cfg.save()
            self.app._tick_clock()
        clock.connect("changed", save_formats)
        datef.connect("changed", save_formats)
        self._list_row(g2, "Time format", None, clock)
        self._list_row(g2, "Date format", None, datef)

    def _set_time_dialog(self):
        set_time_dialog(self.app, lambda: self.show("time"))

    def _tz_picker(self):
        tz_picker(self.app, lambda: self.show("time"))

    def _ntp_dialog(self):
        outer, dlg = self._small_dialog("Time servers")
        custom = system.ntp_servers()
        mode = combo([("auto", "Automatic (Debian pool)"), ("custom", "Custom servers")],
                     "custom" if custom else "auto")
        dlg.pack_start(field("Servers", mode), False, False, 0)
        servers_e = entry(" ".join(custom), "ntp1.example.com ntp2.example.com")
        holder = field("Custom servers", servers_e)
        holder.set_sensitive(bool(custom))
        dlg.pack_start(holder, False, False, 0)
        mode.connect("changed", lambda c: holder.set_sensitive(c.get_active_id() == "custom"))
        status = ui.label("", "small")
        dlg.pack_start(status, False, False, 0)

        def save():
            lst = servers_e.get_text().replace(",", " ").split() if mode.get_active_id() == "custom" else []
            if mode.get_active_id() == "custom" and not lst:
                status.set_markup('<span foreground="#C53030">Enter at least one server.</span>')
                return
            if system.set_ntp_servers(lst):
                self.app.pop_layer()
                GLib.timeout_add(1500, lambda: (self.show("time"), False)[1])
            else:
                status.set_markup('<span foreground="#C53030">Couldn’t save the servers.</span>')
        self._dialog_buttons(dlg, save)
        self.app.push_layer(outer, "light")

    # -- Bluetooth ------------------------------------------------------------
    @staticmethod
    def _bt_icon(kind):
        if "keyboard" in kind:
            return "keyboard_dev"
        if "mouse" in kind or "tablet" in kind:
            return "mouse"
        if "audio" in kind or "head" in kind:
            return "headset"
        return "device"

    def _panel_vpn(self, page):
        from . import vpn
        vpn.panel(self, page)

    def _panel_sound(self, page):
        from . import panels
        panels.sound(self, page)

    def _panel_keyboard(self, page):
        from . import panels
        panels.keyboard(self, page)

    def _panel_remote(self, page):
        from . import panels
        panels.connections(self, page)

    def _panel_diag(self, page):
        from . import panels
        panels.diagnostics(self, page)

    def _panel_bluetooth(self, page):
        ad = system.bt_adapter()
        if not ad["present"]:
            sw = Gtk.Switch(active=False, valign=Gtk.Align.CENTER)
            sw.set_sensitive(False)
            extra = Gtk.Box(spacing=8)
            extra.pack_start(ui.label("Off", "muted small"), False, False, 0)
            extra.pack_start(sw, False, False, 0)
            page.pack_start(self._acc_card("bluetooth", "Bluetooth", "No Bluetooth adapter found", "muted small",
                                           expandable=False, extra=extra)[0], False, False, 0)
            return
        sw = Gtk.Switch(active=ad["powered"], valign=Gtk.Align.CENTER)
        status = (f"On · visible as {ad['name']}" if ad["name"] else "On") if ad["powered"] else "Off"
        card, body = self._acc_card("bluetooth", "Bluetooth", status, "ok small" if ad["powered"] else "muted small",
                                    extra=sw, open_=ad["powered"])
        page.pack_start(card, False, False, 0)

        def toggled(s, *_):
            system.in_background(lambda: system.bt_power(s.get_active()), lambda _r: self.show("bluetooth"))
        sw.connect("notify::active", toggled)

        if not ad["powered"]:
            self._acc_row(body, "Bluetooth is off", "")
            return
        loading = self._acc_row(body, "Paired devices", "Loading…")

        def fill(devs):
            if body.get_ancestor(Gtk.Window) is None:   # panel was closed meanwhile
                return
            body.remove(loading)
            if not devs:
                self._acc_row(body, "No paired devices", "")
            for d in devs:
                row = ui.add_class(Gtk.Box(spacing=12), "acc-row")
                row.pack_start(ui.icon(self._bt_icon(d["kind"]), 18, "#16171A"), False, False, 0)
                col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                col.pack_start(ui.label(d["name"], "body"), False, False, 0)
                col.pack_start(ui.label("Connected" if d["connected"] else "Not connected",
                                        "ok small" if d["connected"] else "muted small"), False, False, 0)
                row.pack_start(col, True, True, 0)
                mac, on = d["mac"], not d["connected"]
                row.pack_start(ui.button("Connect" if on else "Disconnect", "btn",
                                         lambda m=mac, o=on: self._bt_do(lambda: system.bt_connect(m, o))),
                               False, False, 0)
                row.pack_start(ui.button("Remove", "btn",
                                         lambda m=mac: self._bt_do(lambda: system.bt_remove(m))), False, False, 0)
                body.pack_start(row, False, False, 0)
            add = ui.add_class(Gtk.Box(spacing=12), "acc-row")
            add.pack_start(ui.label("Keyboards, mice, headsets and other devices", "muted body"), True, True, 0)
            add.pack_start(ui.button("Add device", "btn btn-primary", self._bt_add), False, False, 0)
            body.pack_start(add, False, False, 0)
            body.show_all()
        system.in_background(system.bt_paired, fill)

    def _bt_do(self, work):
        """Run a Bluetooth action off the UI thread, then refresh the panel."""
        system.in_background(work, lambda _r: self.show("bluetooth"))

    def _bt_add(self):
        outer, dlg = self._small_dialog("Add a Bluetooth device")
        outer.set_size_request(480, -1)
        hint = ui.label("Put the device in pairing mode, then choose it below.", "muted small")
        hint.set_line_wrap(True)
        dlg.pack_start(hint, False, False, 0)
        found = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        found.set_size_request(-1, 160)
        dlg.pack_start(found, False, False, 0)
        status = ui.label("", "small")
        dlg.pack_start(status, False, False, 0)
        again = ui.button("Search again", "btn", lambda: search())
        row = Gtk.Box(spacing=10)
        row.pack_start(again, False, False, 0)
        row.pack_start(Gtk.Box(), True, True, 0)
        row.pack_start(ui.button("Close", "btn", self.app.pop_layer), False, False, 0)
        dlg.pack_start(row, False, False, 0)

        def pair(mac, name):
            status.set_text(f"Pairing with {name}…")
            found.set_sensitive(False)

            def done(res):
                ok, msg = res
                found.set_sensitive(True)
                if ok:
                    self.app.pop_layer()
                    self.show("bluetooth")
                else:
                    status.set_markup(f'<span foreground="#C53030">Couldn’t pair with {GLib.markup_escape_text(name)} ({GLib.markup_escape_text(msg)}).</span>')
            system.in_background(lambda: system.bt_pair(mac), done)

        def show_found(devs):
            for ch in found.get_children():
                found.remove(ch)
            again.set_sensitive(True)
            status.set_text("" if devs else "No devices found. Make sure the device is in pairing mode.")
            for mac, name in devs[:8]:
                r = Gtk.Box(spacing=12)
                r.pack_start(ui.icon("device", 18, "#16171A"), False, False, 0)
                r.pack_start(ui.label(name, "body"), True, True, 0)
                r.pack_start(ui.button("Pair", "btn", lambda m=mac, n=name: pair(m, n)), False, False, 0)
                found.pack_start(r, False, False, 0)
            found.show_all()

        def search():
            again.set_sensitive(False)
            status.set_text("Searching for devices…")
            system.in_background(system.bt_scan, show_found)

        self.app.push_layer(outer, "light")
        search()

    def _copy(self, text):
        from gi.repository import Gdk
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)

    def _small_dialog(self, title):
        return small_dialog(title)

    def _edit_ip(self, a):
        outer, dlg = self._small_dialog("Edit IP settings")
        mode = combo([("auto", "Automatic (DHCP)"), ("manual", "Manual")], a["method"])
        dlg.pack_start(field("IP assignment", mode), False, False, 0)
        addr = entry(a["address"], "192.168.1.42/24")
        gw = entry(a["gateway"], "192.168.1.1")
        manual = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        manual.pack_start(field("IP address / prefix", addr), False, False, 0)
        manual.pack_start(field("Gateway", gw), False, False, 0)
        dlg.pack_start(manual, False, False, 0)
        status = ui.label("", "small")
        dlg.pack_start(status, False, False, 0)
        mode.connect("changed", lambda c: manual.set_sensitive(c.get_active_id() == "manual"))
        manual.set_sensitive(a["method"] == "manual")

        def save():
            m = mode.get_active_id()
            if m == "manual" and "/" not in addr.get_text():
                status.set_markup('<span foreground="#C53030">Use address/prefix, e.g. 192.168.1.42/24</span>')
                return
            status.set_text("Applying…")
            if system.set_ipv4(a["connection"], m, addr.get_text().strip(), gw.get_text().strip()):
                self.app.pop_layer()
                self.show("network")
            else:
                status.set_markup('<span foreground="#C53030">Couldn’t apply these settings.</span>')
        self._dialog_buttons(dlg, save)
        self.app.push_layer(outer, "light")

    def _edit_dns(self, a):
        outer, dlg = self._small_dialog("Edit DNS servers")
        mode = combo([("auto", "Automatic (DHCP)"), ("manual", "Manual")], "manual" if a["dns_manual"] else "auto")
        dlg.pack_start(field("DNS server assignment", mode), False, False, 0)
        servers = entry(", ".join(a["dns"]) if a["dns_manual"] else "", "1.1.1.1, 8.8.8.8")
        holder = field("DNS servers", servers)
        dlg.pack_start(holder, False, False, 0)
        status = ui.label("", "small")
        dlg.pack_start(status, False, False, 0)
        mode.connect("changed", lambda c: holder.set_sensitive(c.get_active_id() == "manual"))
        holder.set_sensitive(a["dns_manual"])

        def save():
            lst = [s.strip() for s in servers.get_text().replace(";", ",").split(",") if s.strip()]
            if mode.get_active_id() == "auto":
                lst = []
            status.set_text("Applying…")
            if system.set_dns_all(lst):
                self.app.pop_layer()
                self.show("network")
            else:
                status.set_markup('<span foreground="#C53030">Couldn’t apply these settings.</span>')
        self._dialog_buttons(dlg, save)
        self.app.push_layer(outer, "light")

    def _edit_dot1x(self, a, cur):
        """802.1X on a wired network: user name + password (PEAP / TTLS) or a
        certificate (TLS). Certificates come from a USB drive."""
        cur = cur or {}
        outer, dlg = self._small_dialog("802.1X authentication")
        outer.set_size_request(500, -1)
        method = combo([("", "Off"), ("peap", "PEAP (user name and password)"),
                        ("ttls", "TTLS (user name and password)"), ("tls", "TLS (certificate)")],
                       cur.get("eap", ""))
        dlg.pack_start(field("Authentication", method), False, False, 0)
        ident = entry(cur.get("identity", ""), "Your network user name")
        pw = entry("", "Unchanged" if cur.get("eap") in ("peap", "ttls") else "", secret=True)
        anon = entry(cur.get("anonymous", ""), "anonymous")
        files = {k: cur.get(k, "") for k in ("ca_cert", "client_cert", "private_key")}
        key_pw = entry("", "", secret=True)

        def file_field(key, title, optional_text):
            box = Gtk.Box(spacing=10)
            name = ui.label("", "muted body")
            name.set_ellipsize(3)
            box.pack_start(name, True, True, 0)

            def chosen(path):
                files[key] = path
                show_name()
            box.pack_start(ui.button("Choose…", "btn", lambda: self._pick_cert(chosen)), False, False, 0)
            clear = ui.button("Remove", "btn", lambda: chosen(""))
            clear.set_no_show_all(True)         # only once a file is chosen
            clear.get_child().show_all()
            box.pack_start(clear, False, False, 0)

            def show_name():
                name.set_text(os.path.basename(files[key]) if files[key] else optional_text)
                clear.set_visible(bool(files[key]))
            show_name()
            return field(title, box)

        pw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        pw_box.pack_start(field("Password", pw), False, False, 0)
        pw_box.pack_start(field("Anonymous identity", anon, optional=True), False, False, 0)
        tls_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        tls_box.pack_start(file_field("client_cert", "User certificate", "None chosen"), False, False, 0)
        tls_box.pack_start(file_field("private_key", "Private key", "None chosen"), False, False, 0)
        tls_box.pack_start(field("Private key password", key_pw, optional=True), False, False, 0)
        common = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        common.pack_start(field("Identity", ident), False, False, 0)
        common.pack_start(pw_box, False, False, 0)
        common.pack_start(tls_box, False, False, 0)
        common.pack_start(file_field("ca_cert", "CA certificate", "None: the server isn’t checked"),
                          False, False, 0)
        dlg.pack_start(common, False, False, 0)
        status = ui.label("", "small")
        dlg.pack_start(status, False, False, 0)

        def sync(*_):
            m = method.get_active_id()
            common.set_visible(bool(m))
            pw_box.set_visible(m in ("peap", "ttls"))
            tls_box.set_visible(m == "tls")
        for w in (common, pw_box, tls_box):
            w.set_no_show_all(True)
            for ch in w.get_children():
                ch.show_all()
        method.connect("changed", sync)
        sync()

        def err(text):
            status.set_markup(f'<span foreground="#C53030">{GLib.markup_escape_text(text)}</span>')

        def save():
            m = method.get_active_id()
            if m and not ident.get_text().strip():
                return err("Enter the identity (your network user name).")
            if m in ("peap", "ttls") and not pw.get_text() and cur.get("eap") not in ("peap", "ttls"):
                return err("Enter the password.")
            if m == "tls" and not (files["client_cert"] and files["private_key"]):
                return err("Choose the user certificate and the private key.")
            s = {"eap": m, "identity": ident.get_text().strip(), "anonymous": anon.get_text().strip(),
                 "password": pw.get_text(), "key_password": key_pw.get_text(), **files}
            if m in ("peap", "ttls") and not s["password"]:
                s.pop("password")                       # keep the saved one
            status.set_text("Applying… (the network reconnects)")
            dlg.set_sensitive(False)

            def done(ok):
                dlg.set_sensitive(True)
                if ok:
                    self.app.pop_layer()
                    self.show("network")
                else:
                    err("Couldn’t connect with these settings. Check them and try again.")
            system.in_background(lambda: system.dot1x_set(a["connection"], s), done)
        self._dialog_buttons(dlg, save)
        self.app.push_layer(outer, "light")

    def _pick_cert(self, on_pick):
        """Certificate and key files from a USB drive, copied into LiteLink's
        settings (kept across reboots) so the drive can be removed afterwards."""
        outer, dlg = self._small_dialog("Choose a file from a USB drive")
        outer.set_size_request(480, -1)
        msg = ui.label("Looking for USB drives…", "muted body", wrap=True)
        dlg.pack_start(msg, False, False, 0)
        lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        dlg.pack_start(lst, False, False, 0)
        row = Gtk.Box(halign=Gtk.Align.END)
        row.pack_start(ui.button("Cancel", "btn", self.app.pop_layer), False, False, 0)
        dlg.pack_start(row, False, False, 0)
        self.app.push_layer(outer, "light")
        suffixes = (".pem", ".crt", ".cer", ".der", ".key", ".p12", ".pfx")

        def work():
            found = []
            for d in system.media_list():
                mp = system.media_mount(d["path"])
                if mp:
                    found += [(p, f"{d['label'] or d['drive']} · {where}")
                              for p, where in system.find_files(mp, suffixes)]
            return found

        def pick(path):
            dest_dir = os.path.expanduser("~/.config/litelink/certs")
            os.makedirs(dest_dir, mode=0o700, exist_ok=True)
            dest = os.path.join(dest_dir, os.path.basename(path))
            try:
                shutil.copyfile(path, dest)
                os.chmod(dest, 0o600)
            except OSError as e:
                msg.set_markup(f'<span foreground="#C53030">Couldn’t copy the file: '
                               f'{GLib.markup_escape_text(e.strerror or str(e))}</span>')
                return
            self.app.pop_layer()
            on_pick(dest)

        def listed(found):
            if lst.get_ancestor(Gtk.Window) is None:
                return
            if not found:
                msg.set_text("No certificate files found. Plug in a USB drive with a .pem, .crt, .cer, "
                             ".der, .key, .p12 or .pfx file.")
                return
            msg.set_text("Certificate and key files on your USB drives:")
            for i, (path, where) in enumerate(found):
                r = ui.add_class(Gtk.Box(spacing=12), "device-row")
                if i < len(found) - 1:
                    ui.add_class(r, "device-row-sep")
                col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                col.pack_start(ui.label(os.path.basename(path), "body"), False, False, 0)
                col.pack_start(ui.label(where, "faint small"), False, False, 0)
                r.pack_start(col, True, True, 0)
                b = ui.button("Use", "btn", lambda p=path: pick(p))
                b.set_valign(Gtk.Align.CENTER)
                r.pack_start(b, False, False, 0)
                lst.pack_start(r, False, False, 0)
            lst.show_all()
        system.in_background(work, listed)

    def _dialog_buttons(self, dlg, on_save):
        row = Gtk.Box(spacing=10, halign=Gtk.Align.END)
        row.pack_start(ui.button("Cancel", "btn", self.app.pop_layer), False, False, 0)
        row.pack_start(ui.button("Save", "btn btn-primary", on_save), False, False, 0)
        dlg.pack_start(row, False, False, 0)

    def _panel_storage(self, page):
        from . import storage
        storage.panel(self, page)

    def _confirm_display(self, output, old, new, seconds=15):
        """'Keep these display settings?' with a countdown. Anything other than
        Keep changes (timeout, Revert, Esc) restores the previous mode, so a
        resolution the monitor can't show never leaves a dark screen."""
        outer, dlg = self._small_dialog("Keep these display settings?")
        msg = ui.label(f"The resolution changed to {new.replace('x', ' × ')}. If you don’t confirm, "
                       f"it goes back to {old.replace('x', ' × ')} automatically.", "muted body")
        msg.set_line_wrap(True)
        msg.set_max_width_chars(46)
        dlg.pack_start(msg, False, False, 0)
        state = {"left": seconds, "timer": None, "done": False}

        def finish(keep):
            if state["done"]:
                return
            state["done"] = True
            if state["timer"]:
                GLib.source_remove(state["timer"])
                state["timer"] = None
            if not keep:
                system.set_mode(output, old)
            else:     # remembered (and re-applied at start) when settings storage exists
                self.app.cfg.data["display_mode"] = {"output": output, "mode": new}
                self.app.cfg.save()
            self.app.pop_layer(entry)
            self.show("display")

        revert_btn, count = ui.count_button("Revert", "btn", lambda: finish(False))
        keep_btn = ui.button("Keep changes", "btn btn-primary", lambda: finish(True))
        row = Gtk.Box(spacing=10, halign=Gtk.Align.END)
        row.pack_start(revert_btn, False, False, 0)
        row.pack_start(keep_btn, False, False, 0)
        dlg.pack_start(row, False, False, 0)

        def tick():
            count.set_text(str(state["left"]))
            if state["left"] <= 0:
                state["timer"] = None
                finish(False)
                return False
            state["left"] -= 1
            return True

        # Esc / closing the dialog any other way counts as Revert.
        entry = self.app.push_layer(outer, "light", on_close=lambda: finish(False))
        tick()
        state["timer"] = GLib.timeout_add_seconds(1, tick)

    def _panel_display(self, page):
        outs = system.displays()
        if not outs:
            page.pack_start(ui.label("No display information available.", "muted body"), False, False, 0)
            return
        d = outs[0]
        # Preview: one tile per screen, drawn at its real proportions and sized
        # relative to the largest screen (like the mockup). Screen 1 is selected.
        preview = ui.add_class(Gtk.Box(), "soft")
        preview.set_size_request(-1, 150)
        tiles = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)

        def dims(o):
            w, _, h = (o.get("current") or "").partition("x")
            return (int(w), int(h)) if w.isdigit() and h.isdigit() else (16, 10)
        sizes = [dims(o) for o in outs]
        big = max(max(w, h) for w, h in sizes)
        for i, (o, (w, h)) in enumerate(zip(outs, sizes), 1):
            scale = 180 / big if len(outs) > 1 else min(180 / w, 112 / h)
            tw, th = max(48, int(w * scale)), max(32, int(h * scale))
            if len(outs) == 1 and th > 112:
                tw, th = int(tw * 112 / th), 112
            tile = ui.add_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER),
                                "mon-tile", *(["active"] if i == 1 else []))
            tile.set_size_request(tw, th)
            inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
            inner.pack_start(ui.label(str(i), "mon-num", xalign=0.5), False, False, 0)
            inner.pack_start(ui.label(o["output"], "mon-name", xalign=0.5), False, False, 0)
            tile.set_center_widget(inner)
            tiles.pack_start(tile, False, False, 0)
        preview.pack_start(tiles, True, True, 0)
        page.pack_start(preview, False, False, 0)
        # Largest first; the monitor's preferred mode is marked.
        def area(m):
            w, _, h = m.partition("x")
            return int(w) * int(h) if w.isdigit() and h.isdigit() else 0
        modes = sorted(d["modes"], key=area, reverse=True)
        res = combo([(m, m.replace("x", " × ") + ("  (recommended)" if m == d.get("preferred") else ""))
                     for m in modes], d["current"])
        page.pack_start(field("Resolution", res), False, False, 0)
        status = ui.label("", "small")
        row = Gtk.Box(spacing=10)
        row.pack_start(status, True, True, 0)

        def apply():
            new, old = res.get_active_id(), d["current"]
            if not new or new == old:
                status.set_text("That's the current resolution.")
                return
            if not system.set_mode(d["output"], new):
                status.set_text("Couldn’t change the resolution.")
                return
            status.set_text("")
            self._confirm_display(d["output"], old, new)
        row.pack_start(ui.button("Apply", "btn btn-primary", apply), False, False, 0)
        page.pack_start(row, False, False, 0)

    # -- System: Standard or Kiosk ---------------------------------------------
    def _panel_mode(self, page):
        cfg = self.app.cfg
        self.set_header("How this device starts. Either mode works with any connection.")
        g = self._list_group(page)
        std = Gtk.RadioButton()
        kiosk = Gtk.RadioButton.new_from_widget(std)
        for r in (std, kiosk):
            r.set_valign(Gtk.Align.CENTER)
        (kiosk if cfg.mode == "kiosk" else std).set_active(True)
        self._list_row(g, "Standard", "Desktop with your connection tiles", std)
        self._list_row(g, "Kiosk", "Starts one connection full screen and nothing else, for shared devices "
                                   "like school labs", kiosk)

        def picked(r, mode):
            if r.get_active() and cfg.data.get("mode") != mode:
                cfg.data["mode"] = mode
                cfg.save()
                self.show("mode")
        std.connect("toggled", picked, "standard")
        kiosk.connect("toggled", picked, "kiosk")
        # Kiosk settings stay visible in Standard mode, disabled until Kiosk is picked
        sec = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        page.pack_start(sec, False, False, 0)
        self._kiosk_section(sec)
        sec.set_sensitive(cfg.mode == "kiosk")
        self._admin_section(page)

    def _kiosk_section(self, page):
        cfg = self.app.cfg
        page.pack_start(ui.add_class(ui.label("KIOSK", "group-title"), "section-title"), False, False, 0)
        g2 = self._list_group(page)
        conn = cfg.kiosk_conn()
        if cfg.connections:
            pick = combo([(c["id"], c["name"]) for c in cfg.connections], conn["id"])
            pick.set_size_request(200, -1)

            def chose(c):
                cfg.data["kiosk_connection"] = c.get_active_id()
                cfg.save()
                self.show("mode")
            pick.connect("changed", chose)
            self._list_row(g2, "Connection", "Opens full screen at startup and after every sign-out", pick)
            if conn.get("protocol") == "web":
                sub = (f"{conn['name']} opens straight away; people sign in on the page itself. "
                       "End session at the top edge clears everything for the next person.")
                value = "On the page"
            elif has_credentials(conn):
                sub = (f"{conn['name']} has a saved username and password, so it signs in by itself. "
                       "Remove the password in the connection to have each person sign in.")
                value = "Automatic"
            else:
                sub = (f"No username and password are saved in {conn['name']}, so each person enters "
                       "their own. Save both in the connection to sign in automatically.")
                value = "Each person"
            self._list_row(g2, "Sign-in", sub, self._value(value))
        else:
            self._list_row(g2, "Connection", "None yet. Add one with the Add connection button "
                                             "on the kiosk screen.", None)
        page.pack_start(ui.label("Kiosk mode starts when you close Settings. To come back here, use the "
                                 "Settings button in the kiosk’s corner.", "muted body", wrap=True),
                        False, False, 0)

    # -- Admin: one password for the device (lock screen, Settings) -------------
    def _admin_section(self, page):
        cfg = self.app.cfg
        page.pack_start(ui.add_class(ui.label("ADMIN", "group-title"), "section-title"), False, False, 0)
        g = self._list_group(page)
        has = cfg.lock_needs_password()
        btn = ui.button("Change password" if has else "Set password", "btn",
                        lambda: set_admin_password_dialog(self.app, lambda: self.show("mode")))
        btn.set_valign(Gtk.Align.CENTER)
        self._list_row(g, "Admin password",
                       "Unlocks the lock screen and, in kiosk mode, Settings" if has else
                       "Not set. Anyone can open Settings and leave kiosk mode.", btn)
        sw = Gtk.Switch(active=bool(cfg.data.get("settings_locked")) and has, valign=Gtk.Align.CENTER)

        def toggled(s, *_):
            cfg.data["settings_locked"] = s.get_active()
            cfg.save()
        sw.connect("notify::active", toggled)
        self._list_row(g, "Ask for it to open Settings",
                       "Kiosk mode always asks" if has else "Set a password first", sw, enabled=has)

    def _panel_about(self, page):
        info = system.about()
        soft = ui.add_class(Gtk.Box(spacing=20), "soft")
        soft.pack_start(ui.picture("logo.png", 150), False, False, 0)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER)
        col.pack_start(ui.label(info["name"], "strong body"), False, False, 0)
        col.pack_start(ui.label("Thin client operating system", "muted small"), False, False, 0)
        soft.pack_start(col, True, True, 0)
        page.pack_start(soft, False, False, 0)
        page.pack_start(self._kv(info["rows"]), False, False, 0)
