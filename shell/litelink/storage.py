"""Settings > Install (live USB) / Storage (installed): persistent settings on the USB stick, and the
LiteLink installer (choose disk -> confirm erase -> progress -> restart)."""
from gi.repository import Gdk, GLib, Gtk

from . import system, ui


# ---------------------------------------------------------------- panel
def panel(settings, page):
    st = system.storage_status()
    installed = st.get("installed", False)
    medium = st.get("medium_disk") or ""

    g = settings._list_group(page)
    if installed:
        where = "Internal disk (installed)"
    elif medium.startswith("/dev/") and not medium.startswith("/dev/sr"):
        parts = ["USB stick" if st.get("medium_removable") else "Disk", st.get("medium_model") or ""]
        if st.get("medium_size"):
            parts.append(system.human_size(st["medium_size"]))
        where = " · ".join(p for p in parts if p)
    else:
        where = "DVD or ISO image (temporary)"
    settings._list_row(g, "Running from", None, settings._value(where))

    if st.get("persistence"):
        settings._list_row(g, "Settings storage", "Connections, Wi-Fi, Bluetooth and time settings are kept",
                           ui.label("Saved", "ok body"))
    elif st.get("can_setup_usb"):
        btn = ui.button("Set up", "btn btn-primary", lambda: usb_setup(settings.app, settings))
        settings._list_row(g, "Settings storage", "Not set up — settings are lost when you restart", btn)
    else:
        why = ("Not enough free space on this USB stick (write the ISO in DD / image mode)"
               if st.get("medium_removable") else "Available on a USB stick or after installing")
        settings._list_row(g, "Settings storage", why, ui.label("Not available", "faint body"), enabled=False)

    g2 = settings._list_group(page)
    if installed:
        settings._list_row(g2, "Install LiteLink OS", "LiteLink is installed on this device",
                           ui.label("Installed", "ok body"))
    else:
        btn = ui.button("Install", "btn btn-primary", lambda: Installer(settings.app).open())
        settings._list_row(g2, "Install LiteLink OS",
                           "Copy LiteLink to the internal disk so it starts without the USB stick. "
                           "Your settings come along.", btn)


# ---------------------------------------------------------------- USB settings storage
def usb_setup(app, settings=None):
    outer = ui.add_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), "dialog")
    outer.set_size_request(460, -1)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=24)
    outer.pack_start(box, True, True, 0)
    box.pack_start(ui.label("Keep settings on this USB stick?", "h2"), False, False, 0)
    msg = ui.label("LiteLink adds a small storage area to the free space on this USB stick. Your connections, "
                   "Wi-Fi networks, Bluetooth devices and time settings are then kept when you restart. "
                   "Nothing else on the stick changes.", "muted body")
    msg.set_line_wrap(True)
    msg.set_max_width_chars(48)
    box.pack_start(msg, False, False, 0)
    bar = Gtk.ProgressBar()
    bar.set_no_show_all(True)
    box.pack_start(bar, False, False, 0)
    status = ui.label("", "small")
    status.set_line_wrap(True)
    box.pack_start(status, False, False, 0)
    row = Gtk.Box(spacing=10, halign=Gtk.Align.END)
    cancel = ui.button("Not now", "btn", app.pop_layer)
    go = ui.button("Set up", "btn btn-primary", lambda: start())
    row.pack_start(cancel, False, False, 0)
    row.pack_start(go, False, False, 0)
    box.pack_start(row, False, False, 0)

    def start():
        go.set_sensitive(False)
        cancel.set_sensitive(False)
        bar.show()

        def prog(pct, text):
            bar.set_fraction(pct / 100)
            status.set_text(text)

        def done(ok, err):
            for ch in row.get_children():
                row.remove(ch)
            bar.hide()
            if ok:
                status.set_markup('<span foreground="#1F7A4D">Done. Restart to start keeping settings.</span>')
                row.pack_start(ui.button("Later", "btn", lambda: (app.pop_layer(),
                                                                   settings and settings.show("storage"))),
                               False, False, 0)
                row.pack_start(ui.button("Restart now", "btn btn-primary", lambda: system.power("reboot")),
                               False, False, 0)
            else:
                status.set_markup(f'<span foreground="#C53030">{GLib.markup_escape_text(err)}</span>')
                row.pack_start(ui.button("Close", "btn", app.pop_layer), False, False, 0)
            row.show_all()
        system.storage_run(["usb-setup"], prog, done)

    app.push_layer(outer, "light")


# ---------------------------------------------------------------- live log ("Show details")
class LogView(Gtk.Box):
    """A "Show details" link that opens a read-only, monospace log. New lines
    are appended live; it follows the end unless the user scrolled up."""

    def __init__(self, lines=None, open_=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.lines = list(lines or [])
        self.toggle = ui.add_class(Gtk.Button(label=""), "link-btn")
        self.toggle.set_halign(Gtk.Align.CENTER)
        self.toggle.connect("clicked", lambda *_: self.set_open(not self.rev.get_reveal_child()))
        self.pack_start(self.toggle, False, False, 0)
        self.rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.scroll = ui.add_class(Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER), "log-scroll")
        self.scroll.set_size_request(-1, 170)
        self.view = ui.add_class(Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                              wrap_mode=Gtk.WrapMode.CHAR), "log-view")
        for side in ("left", "right", "top", "bottom"):
            getattr(self.view, f"set_{side}_margin")(10 if side in ("left", "right") else 8)
        self.buf = self.view.get_buffer()
        self.buf.set_text("\n".join(self.lines))
        # Follow the end until the user scrolls up; scrolling back down resumes.
        # Only a real user scroll (wheel, scrollbar) may stop following: the
        # text view also moves the position while it lays out new lines.
        self.follow = True
        self.user = False
        adj = self.scroll.get_vadjustment()
        adj.connect("changed", lambda a: self.follow and self._to_end())
        adj.connect("value-changed", self._scrolled)
        for w in (self.scroll, self.scroll.get_vscrollbar()):
            w.connect("scroll-event", self._user_scroll)
        bar = self.scroll.get_vscrollbar()
        bar.connect("button-press-event", self._user_scroll)
        bar.connect("button-release-event", lambda *_: (setattr(self, "user", False), False)[1])
        self.scroll.add(self.view)
        self.rev.add(self.scroll)
        self.pack_start(self.rev, False, False, 0)
        self.set_open(open_)

    def set_open(self, on):
        self.rev.set_reveal_child(on)
        self.toggle.set_label("Hide details" if on else "Show details")
        if on:
            self.follow = True
            self._to_end()

    def _user_scroll(self, *_):
        self.user = True
        GLib.timeout_add(300, lambda: (setattr(self, "user", self.user and
                                               self.scroll.get_vscrollbar().has_grab()), False)[1])
        return False

    def _scrolled(self, adj):
        at_end = adj.get_value() >= adj.get_upper() - adj.get_page_size() - 4
        if self.user:
            self.follow = at_end
        elif self.follow and not at_end:
            self._to_end()

    def _to_end(self):
        # Set directly (no idle callback, which a busy main loop on slow
        # hardware may never get to); "changed" calls this again after layout.
        adj = self.scroll.get_vadjustment()
        adj.set_value(adj.get_upper() - adj.get_page_size())

    def append(self, line):
        self.lines.append(line)
        self.buf.insert(self.buf.get_end_iter(), ("\n" if self.buf.get_char_count() else "") + line)
        if self.follow:
            self._to_end()


# ---------------------------------------------------------------- installer
STEPS = [("welcome", "Welcome"), ("time", "Time & region"), ("features", "Features"),
         ("disk", "Storage"), ("confirm", "Confirm"), ("progress", "Install"), ("done", "Finish")]

# Protocols the installer can offer: (key, title, subtitle, available yet).
# RDP is always installed. The others show greyed out until they're built.
PROTOCOLS = [
    ("rdp", "RDP", "Microsoft Remote Desktop", True),
    ("vnc", "VNC", "Screen sharing with any computer", False),
    ("workspaces", "Remote workspaces", "Citrix Workspace, VMware Horizon", False),
    ("webkiosk", "Web kiosk", "LiteOS or any website, full screen. Adds a browser engine (Chromium).", True),
]

# Device features: hardware support installed as its own image module, not a connection type.
DEVICES = [
    ("printers", "Printers", "Print to USB and network printers that work without a driver. Adds a print system.", True),
]

# Web kiosk needs about 2 GB of memory. A "2 GB" device reports a little less
# (the kernel and graphics keep some), so warn below 1.75 GiB.
WEB_MIN_KB = 1835008


def _disk_name(d):
    return f"{d['kind']} · {system.human_size(d['size'])}" + (f" · {d['model']}" if d.get("model") else "")


class Installer:
    """Full-screen install wizard, separate from Settings. Welcome -> time & region
    -> features -> choose storage -> confirm (10 s delayed button) -> install -> finish."""

    def __init__(self, app):
        self.app = app
        self.disk = None
        self.layer = None
        self.rail = {}
        self.mode = "standard"
        self.features = {"rdp"}
        self.maintenance = False

        # Card: step rail on the left, page + footer on the right
        self.card = ui.add_class(Gtk.Box(), "wizard-card")
        self.card.set_size_request(880, 600)
        self.card.set_halign(Gtk.Align.CENTER)
        self.card.set_valign(Gtk.Align.CENTER)

        rail = ui.add_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6), "wizard-rail")
        rail.set_size_request(240, -1)
        logo = ui.picture("logo.png", 128)
        logo.set_halign(Gtk.Align.START)
        rail.pack_start(logo, False, False, 0)
        steps = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=28)
        for i, (key, title) in enumerate(STEPS, 1):
            row = ui.add_class(Gtk.Box(spacing=12), "wizard-step")
            num = ui.add_class(Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER), "wizard-num")
            num.set_size_request(26, 26)
            n = ui.label(str(i), xalign=0.5)
            num.set_center_widget(n)
            row.pack_start(num, False, False, 0)
            row.pack_start(ui.label(title, "body"), False, False, 0)
            steps.pack_start(row, False, False, 0)
            self.rail[key] = (row, num, n)
        rail.pack_start(steps, False, False, 0)
        self.card.pack_start(rail, False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=150)
        right.pack_start(self.stack, True, True, 0)
        self.footer = ui.add_class(Gtk.Box(spacing=10), "wizard-footer")
        right.pack_start(self.footer, False, False, 0)
        self.card.pack_start(right, True, True, 0)

        self.page = Gtk.Box()               # full-screen holder for the card
        self.page.pack_start(self.card, True, True, 0)

    def open(self):
        self.app.close_flyout()
        self.app.clear_layers()             # Settings or any dialog steps aside
        self._step_welcome()
        self.layer = self.app.push_layer(self.page, "wizard", closable=True, center=False)

    def close(self):
        if self.layer:
            self.app.pop_layer(self.layer)

    # -- helpers
    def _page(self, name, title, text=None, head=None):
        old = self.stack.get_child_by_name(name)
        if old:
            self.stack.remove(old)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        ui.add_class(box, "wizard-page")
        box.pack_start(head or ui.label(title, "h1"), False, False, 0)
        if text:
            t = ui.label(text, "muted body", wrap=True)
            t.set_max_width_chars(62)
            box.pack_start(t, False, False, 0)
        # Pages scroll when their content is taller than the card (footer stays put)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                    vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
        scroll.add(box)
        self.stack.add_named(scroll, name)
        return box

    def _buttons(self, left=None, *right):
        for c in self.footer.get_children():
            self.footer.remove(c)
        if left:
            self.footer.pack_start(left, False, False, 0)
        for b in reversed(right):
            self.footer.pack_end(b, False, False, 0)
        self.footer.show_all()

    def _show(self, name):
        keys = [k for k, _ in STEPS]
        cur = keys.index(name)
        for i, k in enumerate(keys):
            row, num, n = self.rail[k]
            for c in ("active", "done"):
                row.get_style_context().remove_class(c)
            if num.get_center_widget() is not n:
                num.set_center_widget(n)
            if i < cur:
                row.get_style_context().add_class("done")
                num.set_center_widget(ui.icon("check", 14, "#FFFFFF", 2.4))
            elif i == cur:
                row.get_style_context().add_class("active")
        self.card.show_all()
        self.stack.set_visible_child_name(name)

    @staticmethod
    def _row(parent, title, subtitle=None, end=None, sep=True, enabled=True):
        """Plain list row: title (+ grey subtitle) left, control right; rows are
        separated by a bottom border, none after the last (sep=False)."""
        row = ui.add_class(Gtk.Box(spacing=16), "wizard-row", *(["wizard-row-sep"] if sep else []))
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        col.pack_start(ui.label(title, "body"), False, False, 0)
        if subtitle:
            col.pack_start(ui.label(subtitle, "muted small", wrap=True), False, False, 0)
        row.pack_start(col, True, True, 0)
        if end is not None:
            end.set_valign(Gtk.Align.CENTER)
            row.pack_end(end, False, False, 0)
        row.set_sensitive(enabled)
        parent.pack_start(row, False, False, 0)
        return row

    # -- 1. welcome
    def _step_welcome(self):
        info = system.build_info()
        head = Gtk.Box(spacing=18, margin_top=28, margin_bottom=8)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER)
        col.pack_start(ui.label("Install LiteLink OS", "h1"), False, False, 0)
        line = " · ".join(x for x in (info["codename"], f"Build {info['build']}" if info["build"] else "",
                                      f"{round(info['size'] / 1e6)} MB" if info["size"] else "") if x)
        if line:
            col.pack_start(ui.label(line, "muted small"), False, False, 0)
        head.pack_start(col, True, True, 0)
        head.pack_end(ui.picture("icon.png", 56), False, False, 0)
        self._page("welcome", "Install LiteLink OS",
                   "LiteLink will be copied to this device's internal storage so it starts without the USB "
                   "stick. The disk you choose is erased; your connections, Wi-Fi networks and settings come "
                   "along. It takes a few minutes.", head=head)
        self._buttons(ui.button("Cancel", "btn", self.close),
                      ui.button("Next", "btn btn-primary", self._step_time))
        self._show("welcome")

    # -- 2. time & region (applied right away, so it's carried onto the disk)
    def _step_time(self):
        from .dialogs import set_time_dialog, tz_label, tz_picker
        import time as _t
        box = self._page("time", "Time & region",
                         "Set the time zone for this device. The clock keeps itself right while it's online.")
        st = system.time_status()
        lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._row(lst, "Time zone", None,
                  ui.button(tz_label(st["timezone"]), "btn", lambda: tz_picker(self.app, self._step_time)))
        auto = Gtk.Switch(active=st["ntp"])
        if st["ntp"] and st["synced"] and st["server"]:
            sub = f"Synchronized with {st['server']}"
        elif st["ntp"]:
            sub = "Uses internet time servers"
        else:
            sub = "Set the date and time below"
        self._row(lst, "Set time automatically", sub, auto)
        stamp = _t.strftime("%-d %B %Y, %H:%M")
        self._row(lst, "Date & time", None,
                  ui.button(stamp, "btn", lambda: set_time_dialog(self.app, self._step_time)),
                  sep=False, enabled=not st["ntp"])

        def toggled(s, *_):
            system.set_ntp(s.get_active())
            GLib.timeout_add(1200, lambda: (self._step_time(), False)[1])
        auto.connect("notify::active", toggled)
        box.pack_start(lst, False, False, 0)
        self._buttons(ui.button("Back", "btn", self._step_welcome),
                      ui.button("Next", "btn btn-primary", self._step_features))
        self._show("time")

    # -- 3. features: mode (changeable later in Settings) + what gets installed
    def _step_features(self):
        box = self._page("features", "Features", "You can change these later in Settings.")
        box.set_spacing(10)
        box.pack_start(ui.label("MODE", "wizard-sec"), False, False, 0)
        modes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        std = Gtk.RadioButton()
        kiosk = Gtk.RadioButton.new_from_widget(std)
        (kiosk if self.mode == "kiosk" else std).set_active(True)
        std.connect("toggled", lambda r: r.get_active() and setattr(self, "mode", "standard"))
        kiosk.connect("toggled", lambda r: r.get_active() and setattr(self, "mode", "kiosk"))
        self._row(modes, "Standard", "Desktop with your connection tiles", std)
        self._row(modes, "Kiosk", "Starts one connection full screen and nothing else, for shared devices "
                  "like school labs", kiosk, sep=False)
        box.pack_start(modes, False, False, 0)

        head = Gtk.Box()
        head.pack_start(ui.label("PROTOCOLS", "wizard-sec"), True, True, 0)
        count = ui.label("", "muted small")
        head.pack_end(count, False, False, 0)
        box.pack_start(head, False, False, 0)

        def recount():
            count.set_text(f"{len(self.features & {k for k, *_ in PROTOCOLS})} of {len(PROTOCOLS)} selected")
        protos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for i, (key, title, sub, available) in enumerate(PROTOCOLS):
            if key == "webkiosk" and not system.web_engine():
                available = False          # this USB stick has no browser engine module
            chk = Gtk.CheckButton(active=key in self.features)
            last = i == len(PROTOCOLS) - 1
            if key == "rdp":       # required: ticked and locked (greyed tick, normal text)
                chk.set_sensitive(False)
                self._row(protos, title, sub, chk, sep=not last)
            else:
                warn = None
                if key == "webkiosk":
                    mem = system.memory_kb()
                    if mem and mem < WEB_MIN_KB:
                        warn = ui.label("", "small", wrap=True)
                        warn.set_markup('<span foreground="#B26A00">This device has {:.1f} GB of memory. Web kiosk '
                                        'needs about 2 GB: pages may be slow or close by themselves.</span>'
                                        .format(mem / 1048576))
                        warn.set_no_show_all(True)

                def toggled(c, k=key, warn=warn):
                    (self.features.add if c.get_active() else self.features.discard)(k)
                    recount()
                    if warn is not None:
                        warn.set_visible(c.get_active())
                chk.connect("toggled", toggled)
                self._row(protos, title, sub, chk, sep=not last and warn is None, enabled=available)
                if warn is not None:
                    warn.set_margin_start(4)
                    warn.set_margin_bottom(10)
                    protos.pack_start(warn, False, False, 0)
                    warn.set_visible(chk.get_active())
        recount()
        box.pack_start(protos, False, False, 0)

        # Devices: each one is its own image module, installed only when ticked
        box.pack_start(ui.label("DEVICES", "wizard-sec"), False, False, 0)
        devs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for i, (key, title, sub, available) in enumerate(DEVICES):
            if key == "printers" and not system.print_engine():
                available = False          # this USB stick has no print module
            chk = Gtk.CheckButton(active=key in self.features)
            chk.connect("toggled", lambda c, k=key: (self.features.add if c.get_active() else self.features.discard)(k))
            self._row(devs, title, sub, chk, sep=i < len(DEVICES) - 1, enabled=available)
        box.pack_start(devs, False, False, 0)
        self._buttons(ui.button("Back", "btn", self._step_time),
                      ui.button("Next", "btn btn-primary", self._step_disk))
        self._show("features")

    # -- 2. choose storage
    def _step_disk(self):
        box = self._page("disk", "Storage", "LiteLink is installed on the disk you pick.")
        disks = system.storage_disks()
        nxt = ui.button("Next", "btn btn-primary", self._step_confirm)
        # Header: label + Refresh (re-scan after plugging in or swapping a disk)
        head = Gtk.Box(margin_top=4)
        head.pack_start(ui.label("Available disks", "field-label"), True, True, 0)
        refresh = ui.button("Refresh", "btn btn-sm", self._step_disk)
        refresh.set_valign(Gtk.Align.CENTER)
        head.pack_end(refresh, False, False, 0)
        box.pack_start(head, False, False, 0)
        if disks:
            # Plain list, no container: rows separated by a bottom border, none after the last
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            first = None
            keep = self.disk["path"] if self.disk else None
            for d in disks:
                rb = Gtk.RadioButton.new_from_widget(first)
                first = first or rb
                inner = Gtk.Box(spacing=12)
                inner.pack_start(ui.icon("storage", 20, "#5A606B"), False, False, 0)
                col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                col.pack_start(ui.label(f"{d['kind']} · {system.human_size(d['size'])}", "strong body"),
                               False, False, 0)
                col.pack_start(ui.label(" · ".join(x for x in (d.get("model"), d["path"]) if x), "muted small"),
                               False, False, 0)
                inner.pack_start(col, True, True, 0)
                # The radio sits at the right end (icon left, choice right); the
                # whole row picks the disk
                rb.set_valign(Gtk.Align.CENTER)
                inner.pack_end(rb, False, False, 0)
                rb.connect("toggled", lambda r, d=d: r.get_active() and setattr(self, "disk", d))
                if d["path"] == keep:
                    rb.set_active(True)
                hit = ui.add_class(Gtk.EventBox(visible_window=False), "clickable")
                hit.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                               | Gdk.EventMask.ENTER_NOTIFY_MASK)
                hit.connect("button-release-event", lambda _w, e, r=rb: (e.button == 1 and r.set_active(True), False)[1])
                hit.add(inner)
                row = ui.add_class(Gtk.Box(), "wizard-disk")
                if d is not disks[-1]:
                    ui.add_class(row, "wizard-disk-sep")
                row.pack_start(hit, True, True, 0)
                group.pack_start(row, False, False, 0)
            if not keep or keep not in [d["path"] for d in disks]:
                self.disk = disks[0]
            box.pack_start(group, False, False, 0)
        else:
            nxt.set_sensitive(False)
            box.pack_start(ui.label("No internal disk was found. LiteLink needs at least 4 GB of internal "
                                    "storage.", "muted body", wrap=True), False, False, 0)
        self._buttons(ui.button("Back", "btn", self._step_features), nxt)
        self._show("disk")

    # -- 3. confirm (delayed: the button counts down from 10 before it enables)
    def _step_confirm(self):
        box = self._page("confirm", "Erase this disk?",
                         f"Everything on {_disk_name(self.disk)} will be erased, including any other operating "
                         "system and its files. This can't be undone.")
        # Last choice before installing: keep the maintenance tools on this device?
        tools = Gtk.CheckButton(active=self.maintenance)
        tools.connect("toggled", lambda c: setattr(self, "maintenance", c.get_active()))
        lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=8)
        self._row(lst, "Include maintenance tools", "Terminal (Ctrl+Alt+T) and hardware tools. "
                  "The USB stick always has them.", tools, sep=False)
        box.pack_start(lst, False, False, 0)
        go, count = ui.count_button("Erase and install", "btn btn-red", self._step_progress)
        go.set_sensitive(False)
        self._buttons(ui.button("Back", "btn", self._step_disk), go)
        self._show("confirm")
        left = [10]
        count.set_text(str(left[0]))

        def tick():
            # Stop if the user went Back or the page was rebuilt
            if go.get_parent() is None or self.stack.get_visible_child_name() != "confirm":
                return False
            left[0] -= 1
            if left[0] > 0:
                count.set_text(str(left[0]))
                return True
            go.set_size_request(go.get_allocated_width(), -1)  # keep width so nothing jumps
            for w in count.get_parent().get_children()[-2:]:   # separator + number
                w.hide()
            go.set_sensitive(True)
            return False
        GLib.timeout_add(1000, tick)

    # -- 4. install
    def _step_progress(self):
        box = self._page("progress", "Installing LiteLink OS")
        anim = ui.FrameAnimation("install/install-*.png")    # brand blue, sits on the white card
        anim.set_halign(Gtk.Align.CENTER)
        holder = ui.add_class(Gtk.Box(halign=Gtk.Align.CENTER, margin_top=12), "install-anim")
        holder.pack_start(anim, False, False, 0)
        box.pack_start(holder, False, False, 0)
        status = ui.label("Starting…", "body", xalign=0.5)
        box.pack_start(status, False, False, 0)
        bar = Gtk.ProgressBar()
        box.pack_start(bar, False, False, 0)
        box.pack_start(ui.label("Don't turn off the device or remove the USB stick.", "faint small", xalign=0.5),
                       False, False, 0)
        # Show details: what the installer is doing right now (commands and their output)
        self.log = LogView()
        box.pack_start(self.log, False, False, 0)
        if self.layer:
            self.layer["closable"] = False        # no Esc / right-click while installing
        self._buttons(None)
        self._show("progress")

        def prog(pct, text):
            bar.set_fraction(pct / 100)
            status.set_text(f"{text}…" if pct < 100 else text)

        feats = ",".join(k for k, *_ in PROTOCOLS + DEVICES if k in self.features)
        system.storage_run(["install", self.disk["path"], "--mode", self.mode, "--features", feats,
                            "--maintenance", "on" if self.maintenance else "off"], prog, self._step_done,
                           on_log=self.log.append)

    # -- 5. finish / failed
    def _step_done(self, ok, err):
        if self.layer:
            self.layer["closable"] = True
        if ok:
            self._page("done", "LiteLink OS is installed",
                       "Remove the USB stick, then restart. LiteLink starts from the internal disk "
                       "with your settings.")
            self._buttons(ui.button("Later", "btn", self.close),
                          ui.button("Restart now", "btn btn-primary", lambda: system.power("reboot")))
        else:
            box = self._page("done", "Installation didn't finish",
                             "Nothing was changed on the USB stick. The internal disk may be partly written; "
                             "you can try again.")
            det = ui.label(err, "details-light", wrap=True)
            det.set_selectable(True)
            det.set_max_width_chars(62)
            box.pack_start(det, False, False, 0)
            # The whole log, already open, for IT (also saved on the USB system)
            log = LogView(lines=getattr(self, "log", None) and self.log.lines, open_=True)
            box.pack_start(log, False, False, 0)
            box.pack_start(ui.label("The log is also saved in /run/litelink/install.log until restart.",
                                    "faint small"), False, False, 0)
            self._buttons(ui.button("Close", "btn", self.close),
                          ui.button("Try again", "btn btn-primary", self._step_disk))
        self._show("done")
