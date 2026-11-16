"""Dock flyouts. Each builder returns (card widget, bottom margin in px) so the
card lines up with its dock button."""
from gi.repository import GLib, Gtk

from . import system as sysinfo, ui

STEP = 68      # dock button + gap
SEP = 13       # separator between the status and system groups


def _bottom(slot_from_bottom):
    """Bottom margin for the n-th dock button counted from the bottom
    (0 = Power, then the separator, 1 = Display, 2 = Volume, 3 = Network)."""
    extra = SEP if slot_from_bottom >= 1 else 0
    return 14 + slot_from_bottom * STEP + extra


def _card(width):
    card = ui.add_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12), "card", "flyout")
    card.set_size_request(width, -1)
    return card


def _link(text, cb):
    b = ui.add_class(Gtk.Button(label=text), "link-btn")
    b.set_halign(Gtk.Align.START)
    b.connect("clicked", lambda *_: cb())
    return b


def network(app):
    card = _card(300)
    card.pack_start(ui.label("Network", "flyout-title"), False, False, 0)
    st = sysinfo.network_status()
    row = Gtk.Box(spacing=12)
    chip = ui.add_class(Gtk.Box(), "ico-chip")
    chip.set_size_request(36, 36)
    chip.pack_start(ui.icon("ethernet" if st["kind"] != "wifi" else "network", 18, "#2659B3"), True, True, 0)
    row.pack_start(chip, False, False, 0)
    col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
    col.pack_start(ui.label(st["name"], "strong body"), False, False, 0)
    if st["state"] == "connected":
        col.pack_start(ui.label(f"Connected · {st['ip']}", "ok small"), False, False, 0)
    else:
        col.pack_start(ui.label("Not connected", "muted small"), False, False, 0)
    row.pack_start(col, True, True, 0)
    card.pack_start(row, False, False, 0)

    wifi = Gtk.Box(spacing=8)
    present = sysinfo.wifi_present()
    col_w = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    col_w.pack_start(ui.label("Wi-Fi", "body"), False, False, 0)
    if not present:
        col_w.pack_start(ui.label("No Wi-Fi adapter found", "faint small"), False, False, 0)
    wifi.pack_start(col_w, True, True, 0)
    sw = Gtk.Switch(active=present and sysinfo.wifi_enabled(), valign=Gtk.Align.CENTER)
    if present:
        sw.connect("notify::active", lambda s, *_: sysinfo.wifi_set(s.get_active()))
    else:
        sw.set_sensitive(False)
        wifi.set_opacity(0.5)
    wifi.pack_start(sw, False, False, 0)
    card.pack_start(Gtk.Separator(), False, False, 0)
    card.pack_start(wifi, False, False, 0)
    card.pack_start(_link("Network settings", lambda: app.open_settings("network")), False, False, 0)
    return card, _bottom(3)


def volume(app):
    card = _card(280)
    card.pack_start(ui.label("Volume", "flyout-title"), False, False, 0)
    level, muted = sysinfo.volume_get()
    card.pack_start(ui.label(sysinfo.sink_name(), "body"), False, False, 0)
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    scale.set_draw_value(False)
    scale.set_value(level)
    scale.connect("value-changed", lambda s: sysinfo.volume_set(int(s.get_value())))
    card.pack_start(scale, False, False, 0)
    mute = Gtk.CheckButton(label="Mute", active=muted)
    mute.connect("toggled", lambda c: sysinfo.mute_set(c.get_active()))
    card.pack_start(mute, False, False, 0)
    return card, _bottom(2)


def display(app):
    card = _card(280)
    card.pack_start(ui.label("Display", "flyout-title"), False, False, 0)
    outs = sysinfo.displays()
    if outs:
        d = outs[0]
        rate = f" · {round(float(d['rate']))} Hz" if d["rate"] else ""
        card.pack_start(ui.label(d["current"].replace("x", " × ") + rate, "strong body"), False, False, 0)
        n = len(outs)
        card.pack_start(ui.label(f"{n} monitor{'s' if n > 1 else ''} detected", "muted small"), False, False, 0)
    else:
        card.pack_start(ui.label("No display information", "muted small"), False, False, 0)
    card.pack_start(_link("Display settings", lambda: app.open_settings("display")), False, False, 0)
    return card, _bottom(1)


def settings(app):
    card = ui.add_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), "card")
    card.set_size_request(260, -1)
    head = ui.add_class(Gtk.Box(), "flyout-header")
    head.pack_start(ui.label("Settings"), False, False, 0)
    card.pack_start(head, False, False, 0)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=6, margin_bottom=8)
    from .dialogs import READY_PANELS, SETTINGS_GROUPS
    for title, items in SETTINGS_GROUPS:
        g = ui.label(title.upper(), "group-title")
        g.set_margin_start(16)
        g.set_margin_top(10)
        g.set_margin_bottom(4)
        body.pack_start(g, False, False, 0)
        for key, text, _icon in items:
            b = ui.add_class(Gtk.Button(), "row-btn")
            lb = ui.label(text)
            b.add(lb)
            b.connect("clicked", lambda _b, k=key: app.open_settings(k))
            if key not in READY_PANELS:
                b.set_sensitive(False)
            body.pack_start(b, False, False, 0)
    # Last item: new connections are added here (the desktop only lists real ones)
    body.pack_start(ui.add_class(Gtk.Box(margin_top=6, margin_bottom=6), "menu-sep"), False, False, 0)
    add = ui.add_class(Gtk.Button(), "row-btn")
    row = Gtk.Box(spacing=10)
    row.pack_start(ui.icon("plus", 16, "#2659B3", 2.2), False, False, 0)
    row.pack_start(ui.label("Add connection", "add-link"), False, False, 0)
    add.add(row)
    add.connect("clicked", lambda *_: app.edit_connection(None))
    body.pack_start(add, False, False, 0)
    card.pack_start(body, False, False, 0)
    return card, -14          # negative = anchored to the top (opened from the kite button)
