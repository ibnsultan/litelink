"""Settings > VPN: WireGuard tunnels.

Add tunnel opens a file import first: insert a USB drive, pick its .conf file,
check the summary, import. "Enter/Edit the details manually" switches to a form
(pre-filled from the chosen file); its Back returns to the import. Editing an
existing tunnel opens the form directly (Cancel instead of Back).
Root work happens in litelink-media (read-only USB mounts) and litelink-vpn
(NetworkManager WireGuard connections)."""
import os

from gi.repository import GLib, Gtk

from . import system, ui
from .dialogs import entry, field

POLL_MS = 1500


def _size(n):
    return system.human_size(n) if n else "0 B"


def _routes(allowed):
    nets = [a.strip() for a in (allowed or "").split(",") if a.strip()]
    if any(n in ("0.0.0.0/0", "::/0") for n in nets):
        return f"{', '.join(nets)} · all traffic"
    return f"{', '.join(nets)} · only these networks" if nets else "—"


# ---------------------------------------------------------------- Settings panel
def panel(settings, page):
    settings.set_header(
        "WireGuard tunnels to private networks. A connection can bring its tunnel up by itself "
        "(set it in the connection's settings).",
        ui.button("Add tunnel", "btn btn-primary", lambda: AddTunnel(settings).open()))

    holder = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    page.pack_start(holder, True, True, 0)
    status = ui.label("", "small", wrap=True)
    page.pack_start(status, False, False, 0)

    def fill(tunnels):
        if holder.get_ancestor(Gtk.Window) is None:      # panel closed meanwhile
            return
        for ch in holder.get_children():
            holder.remove(ch)
        if not tunnels:
            holder.pack_start(empty_state("vpn", "No tunnels",
                                          "Add a tunnel with the WireGuard file or the details "
                                          "from your IT team."), True, True, 0)
        for i, t in enumerate(tunnels):
            holder.pack_start(_card(settings, t, status, open_=(i == 0)), False, False, 0)
        holder.show_all()
    system.in_background(system.vpn_list, fill)


def empty_state(icon_name, title, text):
    """Native-style status page: a large faint icon, a title and one line of
    text, centred in the free space. No card, no tinted chip."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8,
                  halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    box.set_size_request(-1, 380)
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
    icon = ui.icon(icon_name, 64, "#B8BEC8", 1.4)
    icon.set_margin_bottom(8)
    inner.pack_start(icon, False, False, 0)
    inner.pack_start(ui.label(title, "empty-title", xalign=0.5), False, False, 0)
    sub = ui.label(text, "muted body", xalign=0.5, wrap=True)
    sub.set_max_width_chars(60)
    inner.pack_start(sub, False, False, 0)
    box.pack_start(inner, True, False, 0)
    return box


def _card(settings, t, status, open_):
    sw = Gtk.Switch(active=t["active"], valign=Gtk.Align.CENTER)
    st = f"Connected · {t['address'].split('/')[0]}" if t["active"] else "Not connected"
    card, body = settings._acc_card("vpn", t["name"], st, "ok small" if t["active"] else "muted small",
                                    extra=sw, open_=open_)

    def toggled(s, *_):
        s.set_sensitive(False)

        def done(res):
            ok, err = res
            if not ok:
                status.set_markup(f'<span foreground="#C53030">Couldn’t '
                                  f'{"connect" if s.get_active() else "disconnect"} '
                                  f'{GLib.markup_escape_text(t["name"])}: {GLib.markup_escape_text(err)}</span>')
            settings.show("vpn")
        system.in_background(lambda: system.vpn_set(t["uuid"], s.get_active()), done)
    sw.connect("notify::active", toggled)

    settings._acc_row(body, "Server", t["endpoint"] or "—")
    settings._acc_row(body, "Address", t["address"] or "—")
    if t["dns"]:
        settings._acc_row(body, "DNS", t["dns"])
    settings._acc_row(body, "Routes", _routes(t["allowed_ips"]))
    if t["active"]:
        settings._acc_row(body, "Traffic", f"↓ {_size(t['rx'])}   ↑ {_size(t['tx'])}")

    row = ui.add_class(Gtk.Box(spacing=10), "acc-row")
    auto = Gtk.CheckButton(label="Connect automatically at startup", active=t["autoconnect"])
    auto.connect("toggled", lambda c: system.in_background(
        lambda: system.vpn_autoconnect(t["uuid"], c.get_active()), lambda _r: None))
    row.pack_start(auto, True, True, 0)
    row.pack_start(ui.button("Edit", "btn", lambda: AddTunnel(settings, edit=t).open()), False, False, 0)
    row.pack_start(ui.button("Remove", "btn", lambda: _confirm_remove(settings, t)), False, False, 0)
    body.pack_start(row, False, False, 0)
    return card


def _confirm_remove(settings, t):
    from .dialogs import small_dialog
    outer, dlg = small_dialog("Remove this tunnel?")
    msg = ui.label(f"{t['name']} and its keys are removed from this device.", "muted body", wrap=True)
    dlg.pack_start(msg, False, False, 0)
    row = Gtk.Box(spacing=10, halign=Gtk.Align.END)
    row.pack_start(ui.button("Cancel", "btn", settings.app.pop_layer), False, False, 0)

    def go():
        settings.app.pop_layer()
        system.in_background(lambda: system.vpn_remove(t["uuid"]), lambda _r: settings.show("vpn"))
    row.pack_start(ui.button("Remove", "btn btn-red", go), False, False, 0)
    dlg.pack_start(row, False, False, 0)
    settings.app.push_layer(outer, "light")


# ---------------------------------------------------------------- Add / edit tunnel
class AddTunnel:
    """One dialog with two pages: import (default) and manual."""

    def __init__(self, settings, edit=None):
        self.settings = settings
        self.app = settings.app
        self.edit = edit                 # existing tunnel (dict from vpn_list) or None
        self.layer = None
        self.closed = False
        self.mounted = {}                # device -> {path, drive, mountpoint, ...} for drives with files
        self.files = []                  # (file path, folder label, device) across all those drives
        self.picked = None               # (path, parsed dict)
        self.timer = None
        self.busy = False                # mounting / scanning in progress
        self.note = None                 # why the waiting page is shown again (no files, unreadable)
        self.skip = set()                # drives already checked that had no .conf files

        self.outer = ui.add_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), "dialog")
        self.outer.set_size_request(580, -1)
        head = Gtk.Box(spacing=12, margin_top=18, margin_start=20, margin_end=14)
        head.pack_start(ui.picture("icon.png", 28), False, False, 0)
        tcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        self.title = ui.label("", "h2")
        self.subtitle = ui.label("", "muted small")
        tcol.pack_start(self.title, False, False, 0)
        tcol.pack_start(self.subtitle, False, False, 0)     # subtext under the title
        head.pack_start(tcol, True, True, 0)
        close = ui.add_class(Gtk.Button(), "icon-btn")
        close.add(ui.icon("close", 18, "#5A606B", 2))
        close.connect("clicked", lambda *_: self.close())
        head.pack_end(close, False, False, 0)
        self.outer.pack_start(head, False, False, 0)

        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, margin=20)
        self.body.set_size_request(-1, 320)
        self.outer.pack_start(self.body, True, True, 0)
        self.error = ui.label("", "small", wrap=True)
        self.error.set_margin_start(20)
        self.error.set_margin_end(20)
        self.outer.pack_start(self.error, False, False, 0)
        self.footer = ui.add_class(Gtk.Box(spacing=10), "dialog-footer")
        self.outer.pack_start(self.footer, False, False, 0)

    # -- lifecycle
    def open(self):
        if self.edit:
            self._manual(self._from_tunnel(self.edit))
        else:
            self._import_page()
        self.layer = self.app.push_layer(self.outer, "light", on_close=self._cleanup)

    def close(self):
        if self.layer:
            self.app.pop_layer(self.layer)

    def _cleanup(self):
        self.closed = True
        self._stop_poll()
        if self.mounted:
            system.in_background(system.media_unmount, lambda _r: None)     # every drive mounted here
            self.mounted = {}

    def _clear(self):
        for box in (self.body, self.footer):
            for ch in box.get_children():
                box.remove(ch)
        self.error.set_text("")

    def _footer(self, left, *right):
        if left:
            self.footer.pack_start(left, False, False, 0)
        for b in reversed(right):
            self.footer.pack_end(b, False, False, 0)
        self.outer.show_all()

    def _fail(self, text):
        self.error.set_markup(f'<span foreground="#C53030">{GLib.markup_escape_text(text)}</span>')

    # -- import page
    def _import_page(self):
        self._clear()
        self.title.set_text("Add tunnel")
        self.subtitle.set_text("WireGuard · import a .conf file")
        if self.picked:
            self._ready()
        elif self.files:
            self._file_list()
            self._start_poll()           # keep watching: drives can be pulled out or added
        else:
            self._waiting(self.note)
            self._start_poll()
        import_btn = ui.button("Import", "btn btn-primary", self._do_import)
        import_btn.set_sensitive(self.picked is not None)
        link = ui.add_class(Gtk.Button(label="Edit the details manually" if self.picked
                                       else "Enter the details manually"), "link-btn")
        link.connect("clicked", lambda *_: self._manual(self._from_file()))
        self._footer(link, ui.button("Cancel", "btn", self.close), import_btn)

    def _waiting(self, note=None):
        zone = ui.add_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), "drop-zone")
        zone.set_size_request(-1, 260)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.CENTER)
        zone.pack_start(box, True, True, 0)
        chip = ui.add_class(Gtk.Box(halign=Gtk.Align.CENTER), "ico-chip", "big")
        chip.pack_start(ui.icon("usb", 26, "#2659B3"), True, True, 0)
        box.pack_start(chip, False, False, 0)
        box.pack_start(ui.label("Insert the USB drive with your WireGuard file", "strong body", xalign=0.5),
                       False, False, 0)
        sub = ui.label(note or "Files ending in .conf are found automatically. "
                               "The drive is only read, never changed.", "muted small", xalign=0.5, wrap=True)
        sub.set_max_width_chars(52)
        box.pack_start(sub, False, False, 0)
        box.pack_start(ui.label("Waiting for a drive…", "faint small", xalign=0.5), False, False, 0)
        self.body.pack_start(zone, True, True, 0)

    def _drive_name(self, dev):
        return (self.mounted.get(dev) or {}).get("drive") or "USB drive"

    def _file_list(self):
        devs = list(self.mounted)
        several = len(devs) > 1
        top = Gtk.Box(spacing=8)
        top.pack_start(ui.icon("usb", 16, "#5A606B"), False, False, 0)
        top.pack_start(ui.label(f"{len(devs)} drives" if several else self._drive_name(devs[0] if devs else ""),
                                "muted small"), True, True, 0)
        n = len(self.files)
        top.pack_end(ui.label(f"{n} file{'s' if n != 1 else ''} found", "muted small"), False, False, 0)
        self.body.pack_start(top, False, False, 0)
        lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for i, (path, folder, dev) in enumerate(self.files):
            where = f"{self._drive_name(dev)} · {folder}" if several else folder
            b = ui.add_class(Gtk.Button(), "file-row", *(["file-row-sep"] if i < n - 1 else []))
            row = Gtk.Box(spacing=12)
            chip = ui.add_class(Gtk.Box(), "ico-chip", "small")
            chip.pack_start(ui.icon("file", 18, "#2659B3"), True, True, 0)
            row.pack_start(chip, False, False, 0)
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
            col.pack_start(ui.label(os.path.basename(path), "body"), False, False, 0)
            col.pack_start(ui.label(where, "muted small"), False, False, 0)
            row.pack_start(col, True, True, 0)
            row.pack_start(ui.icon("chev_right", 16, "#8A8F98", 2), False, False, 0)
            b.add(row)
            b.connect("clicked", lambda _b, p=path: self._pick(p))
            lst.pack_start(b, False, False, 0)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_size_request(-1, 250)
        scroll.add(lst)
        self.body.pack_start(scroll, True, True, 0)

    def _pick(self, path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read(64 * 1024)
        except OSError as e:
            self._fail(f"Couldn’t read {os.path.basename(path)}: {e.strerror}")
            return
        d = system.parse_wireguard(text)
        if not d["private_key"] or not d["public_key"]:
            self._fail(f"{os.path.basename(path)} isn’t a WireGuard file (no [Interface] / [Peer] keys).")
            return
        self.picked = (path, d)
        self._stop_poll()
        self._import_page()

    def _ready(self):
        path, d = self.picked
        filebox = ui.add_class(Gtk.Box(spacing=12), "file-card")
        chip = ui.add_class(Gtk.Box(), "ico-chip", "small")
        chip.pack_start(ui.icon("file", 18, "#2659B3"), True, True, 0)
        filebox.pack_start(chip, False, False, 0)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        col.pack_start(ui.label(os.path.basename(path), "body"), False, False, 0)
        dev = next((f[2] for f in self.files if f[0] == path), "")
        col.pack_start(ui.label(self._drive_name(dev), "muted small"), False, False, 0)
        filebox.pack_start(col, True, True, 0)

        def another():
            self.picked = None
            self._import_page()
        b = ui.button("Choose another", "btn", another)
        b.set_valign(Gtk.Align.CENTER)
        filebox.pack_end(b, False, False, 0)
        self.body.pack_start(filebox, False, False, 0)

        stem = os.path.splitext(os.path.basename(path))[0].replace("_", " ").replace("-", " ").strip()
        self.name_e = entry(stem[:1].upper() + stem[1:], "e.g. Office VPN")
        self.body.pack_start(field("Name", self.name_e), False, False, 0)

        grid = ui.add_class(Gtk.Grid(column_spacing=12, row_spacing=8), "soft", "summary")
        rows = [("Server", d["endpoint"] or "—"), ("Address", d["address"] or "—")]
        if d["dns"]:
            rows.append(("DNS", d["dns"]))
        rows.append(("Routes", _routes(d["allowed_ips"])))
        if d["peers"] > 1:
            rows.append(("Note", f"The file has {d['peers']} servers; the first is used"))
        for i, (k, v) in enumerate(rows):
            kl = ui.label(k, "muted small")
            kl.set_size_request(90, -1)
            grid.attach(kl, 0, i, 1, 1)
            vl = ui.label(v, "small")
            vl.set_line_wrap(True)
            vl.set_max_width_chars(48)
            grid.attach(vl, 1, i, 1, 1)
        self.body.pack_start(grid, False, False, 0)
        self.auto_c = Gtk.CheckButton(label="Connect automatically at startup")
        self.body.pack_start(self.auto_c, False, False, 0)

    def _do_import(self):
        if not self.picked:
            return
        d = dict(self.picked[1])
        d["name"] = self.name_e.get_text().strip()
        d["autoconnect"] = self.auto_c.get_active()
        self._save(d)

    # -- polling for USB drives (import page only)
    def _start_poll(self):
        if not self.timer and not self.closed:
            self.timer = GLib.timeout_add(POLL_MS, self._poll)
            self._poll()

    def _stop_poll(self):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None

    def _poll(self):
        if self.closed:
            return False

        def found(drives):
            if self.closed or self.picked or self.busy:
                return
            present = {d["path"]: d for d in drives}
            self.skip &= set(present)           # unplugged drives are looked at again next time
            gone = [dev for dev in self.mounted if dev not in present]
            fresh = [d for dev, d in present.items() if dev not in self.mounted and dev not in self.skip]
            if gone:                             # pulled out: drop its files, release the mount
                for dev in gone:
                    self.mounted.pop(dev, None)
                    system.in_background(lambda dev=dev: system.media_unmount(dev), lambda _r: None)
                self.files = [f for f in self.files if f[2] not in gone]
                self.note = None
            if fresh:
                self._open_drives(fresh)         # redraws when the drives are read
            elif gone:
                self._import_page()
        system.in_background(system.media_list, found)
        return True

    def _open_drives(self, drives):
        """Mount and scan every new drive; keep the ones with .conf files."""
        self.busy = True

        def work():
            out = []
            for d in drives:
                mp = system.media_mount(d["path"])
                files = system.find_files(mp, ".conf") if mp else []
                if mp and not files:
                    system.media_unmount(d["path"])
                out.append((d, mp, files))
            return out

        def done(results):
            self.busy = False
            if self.closed:
                system.in_background(system.media_unmount, lambda _r: None)
                return
            notes = []
            for d, mp, files in results:
                if not mp:
                    self.skip.add(d["path"])
                    notes.append(f"Couldn’t read the drive in {d['drive']}.")
                elif not files:
                    self.skip.add(d["path"])
                    notes.append(f"No .conf files on {d['drive']}.")
                else:
                    self.mounted[d["path"]] = dict(d, mountpoint=mp)
                    self.files += [(p, folder, d["path"]) for p, folder in files]
            if not self.files and notes:
                self.note = " ".join(notes) + " Use another drive, or enter the details manually."
            if not self.picked:
                self._import_page()
        system.in_background(work, done)

    # -- manual page
    def _from_file(self):
        if not self.picked:
            return {}
        d = dict(self.picked[1])
        d["name"] = self.name_e.get_text().strip() if hasattr(self, "name_e") else ""
        return d

    @staticmethod
    def _from_tunnel(t):
        return {"uuid": t["uuid"], "name": t["name"], "private_key": "", "address": t["address"],
                "dns": t["dns"], "public_key": t["public_key"], "endpoint": t["endpoint"],
                "allowed_ips": t["allowed_ips"], "preshared_key": "", "keepalive": t["keepalive"],
                "autoconnect": t["autoconnect"], "has_preshared_key": t.get("has_preshared_key")}

    def _manual(self, d):
        self._stop_poll()
        self._clear()
        editing = bool(self.edit)
        self.title.set_text("Edit tunnel" if editing else "Add tunnel")
        self.subtitle.set_text("WireGuard" if editing else
                               f"WireGuard · from {os.path.basename(self.picked[0])}" if self.picked
                               else "WireGuard · enter the details")
        keep = "Leave empty to keep the current key"
        f = {
            "name": entry(d.get("name", ""), "e.g. Office VPN"),
            "private_key": entry(d.get("private_key", ""), keep if editing else "From your IT team", secret=True),
            "address": entry(d.get("address", ""), "10.8.0.2/32"),
            "dns": entry(d.get("dns", ""), "10.8.0.1"),
            "public_key": entry(d.get("public_key", ""), "From your IT team"),
            "endpoint": entry(d.get("endpoint", ""), "vpn.example.com:51820"),
            "allowed_ips": entry(d.get("allowed_ips", ""), "10.0.0.0/8"),
            "preshared_key": entry(d.get("preshared_key", ""),
                                   keep if editing and d.get("has_preshared_key") else "From your IT team",
                                   secret=True),
            "keepalive": entry(d.get("keepalive", ""), "25 seconds"),
        }
        for k in ("private_key", "public_key"):
            ui.add_class(f[k], "mono")

        grid = Gtk.Grid(column_spacing=16, row_spacing=12, column_homogeneous=True)
        r = 0

        def put(widget, col=0, span=2):
            nonlocal r
            grid.attach(widget, col, r, span, 1)
            if col + span >= 2:
                r += 1
        put(field("Name", f["name"]))
        put(ui.label("THIS DEVICE", "wizard-sec"))
        put(field("Private key", f["private_key"]))
        put(field("Address", f["address"]), 0, 1)
        put(field("DNS", f["dns"], optional=True), 1, 1)
        put(ui.label("SERVER", "wizard-sec"))
        put(field("Public key", f["public_key"]))
        put(field("Endpoint", f["endpoint"]), 0, 1)
        put(field("Allowed IPs", f["allowed_ips"]), 1, 1)
        put(ui.label("Only these networks go through the VPN. Use 0.0.0.0/0 to send all traffic through it.",
                     "muted small", wrap=True))
        put(field("Preshared key", f["preshared_key"], optional=True), 0, 1)
        put(field("Keepalive", f["keepalive"], optional=True), 1, 1)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_size_request(-1, 470)
        scroll.add(grid)
        self.body.pack_start(scroll, True, True, 0)

        def save():
            data = {k: w.get_text().strip() for k, w in f.items()}
            data["uuid"] = d.get("uuid", "")
            data["autoconnect"] = d.get("autoconnect", False)
            self._save(data)
        back = (ui.button("Cancel", "btn", self.close) if editing
                else ui.button("Back", "btn", self._import_page))
        self._footer(back, ui.button("Save", "btn btn-primary", save))

    # -- save
    def _save(self, data):
        self.footer.set_sensitive(False)
        self.error.set_text("Saving…")

        def done(res):
            ok, err = res
            self.footer.set_sensitive(True)
            if ok:
                self.close()
                self.settings.show("vpn")
            else:
                self._fail(err or "The tunnel couldn’t be saved.")
        system.in_background(lambda: system.vpn_save(data), done)
