"""Dock flyouts. Each builder returns (card widget, bottom margin in px) so the
card lines up with its dock button."""
from gi.repository import Gdk, GLib, Gtk

from . import system as sysinfo, ui, wifi

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


def _fit(widget, room):
    """widget in a scroll area that grows with it up to the screen's height less room
    (px taken by the card's margins and header), then scrolls: short screens, seats."""
    sw = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC)
    sw.set_propagate_natural_height(True)
    sw.set_max_content_height(max(160, Gdk.Screen.get_default().get_height() - room))
    sw.add(widget)
    return sw


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
    if not present:
        sw.set_sensitive(False)
        wifi.set_opacity(0.5)
    wifi.pack_start(sw, False, False, 0)
    card.pack_start(Gtk.Separator(), False, False, 0)
    card.pack_start(wifi, False, False, 0)
    # Nearby networks, to join right here (filled in the background)
    nets = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    card.pack_start(nets, False, False, 0)
    if present and sysinfo.wifi_enabled():
        nets.pack_start(ui.label("Looking for networks…", "faint small"), False, False, 0)
        sysinfo.in_background(lambda: (sysinfo.wifi_list(), sysinfo.wifi_current()),
                              lambda res: _fill_networks(app, nets, *res))

    # VPN: which tunnel is up (filled in the background); opens Settings > VPN
    card.pack_start(Gtk.Separator(), False, False, 0)
    vpn_btn = ui.add_class(Gtk.Button(), "flyout-row")
    vrow = Gtk.Box(spacing=8)
    vcol = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
    vcol.pack_start(ui.label("VPN", "body"), False, False, 0)
    vstat = ui.label(" ", "faint small")
    vcol.pack_start(vstat, False, False, 0)
    vrow.pack_start(vcol, True, True, 0)
    vrow.pack_start(ui.icon("chev_right", 16, "#8A8F98", 2), False, False, 0)
    vpn_btn.add(vrow)
    vpn_btn.connect("clicked", lambda *_: app.open_settings("vpn"))
    card.pack_start(vpn_btn, False, False, 0)

    def fill(tunnels):
        if vstat.get_ancestor(Gtk.Window) is None:      # menu closed meanwhile
            return
        up = [t["name"] for t in tunnels if t["active"]]
        ctx = vstat.get_style_context()
        ctx.remove_class("faint")
        if up:
            vstat.set_text(f"{', '.join(up)} · connected")
            ctx.add_class("ok")
        else:
            vstat.set_text("Not connected" if tunnels else "No tunnels")
            ctx.add_class("muted")
    sysinfo.in_background(sysinfo.vpn_list, fill)

    card.pack_start(_link("Network settings", lambda: _admin(app, lambda: app.open_settings("network"))),
                    False, False, 0)
    if present:
        def wifi_toggled(s, *_):
            on = s.get_active()
            if _needs_admin(app):
                s.handler_block_by_func(wifi_toggled)
                s.set_active(not on)             # undone until the admin password is entered
                s.handler_unblock_by_func(wifi_toggled)
                _admin(app, lambda: sysinfo.wifi_set(on))
            else:
                sysinfo.wifi_set(on)
                GLib.timeout_add(1500, lambda: (app.flyout_name == "network" and app.toggle_flyout("network", reopen=True),
                                                False)[1])
        sw.connect("notify::active", wifi_toggled)
    return card, _bottom(3)


# -- Wi-Fi from the flyout -----------------------------------------------------------------
def _needs_admin(app):
    """Changing the device's network from the kiosk is for admins (when a password is set)."""
    return app.kiosk is not None and app.cfg.lock_needs_password()


def _admin(app, action):
    if _needs_admin(app):
        app.close_flyout()
        app.kiosk.ask_admin(action)
    else:
        action()


def _fill_networks(app, box, found, current):
    if box.get_ancestor(Gtk.Window) is None:          # flyout closed meanwhile
        return
    for c in box.get_children():
        box.remove(c)
    if not found:
        box.pack_start(ui.label("No networks found", "faint small"), False, False, 0)
    for n in found:
        n["active"] = n.get("active") or n["ssid"] == current
    found = sorted(found, key=lambda n: not n["active"])      # the connected network first
    saved = sysinfo.wifi_saved()
    group = wifi.Group()
    refresh = lambda: app.flyout_name == "network" and app.toggle_flyout("network", reopen=True)  # noqa: E731
    # In the kiosk (with an admin password) joining goes through Settings
    admin = (lambda: _admin(app, lambda: app.open_settings("network"))) if _needs_admin(app) else None
    for n in found[:6]:
        if n["active"]:
            box.pack_start(_connected_row(n), False, False, 0)
        else:
            box.pack_start(wifi.NetworkRow(n, group, refresh, saved=n["ssid"] in saved, on_click=admin),
                           False, False, 0)
    box.show_all()


def _connected_row(n):
    row = ui.add_class(Gtk.Box(spacing=10), "flyout-static-row")
    row.pack_start(ui.icon(wifi.signal_icon(n["signal"]), 16, "#3F434B"), False, False, 0)
    name = ui.label(n["ssid"], "body")
    name.set_ellipsize(3)
    row.pack_start(name, True, True, 0)
    row.pack_start(ui.label("Connected", "ok small"), False, False, 0)
    return row


def volume(app):
    """Speaker button (mute) · slider · percentage; the output device below it,
    or the outputs to pick from when there are several."""
    card = _card(300)
    card.pack_start(ui.label("Volume", "flyout-title"), False, False, 0)
    level, muted = sysinfo.volume_get()
    state = {"muted": muted}

    row = Gtk.Box(spacing=8)
    spk = ui.add_class(Gtk.Button(), "icon-btn")
    spk.set_valign(Gtk.Align.CENTER)
    row.pack_start(spk, False, False, 0)
    scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
    scale.set_draw_value(False)
    scale.set_value(level)
    scale.set_valign(Gtk.Align.CENTER)
    row.pack_start(scale, True, True, 0)
    pct = ui.label(f"{level}%", "muted small", xalign=1.0)
    pct.set_width_chars(6)              # fits "Muted" too, so the slider keeps its length
    row.pack_start(pct, False, False, 0)
    card.pack_start(row, False, False, 0)

    def show_mute():
        m = state["muted"]
        if spk.get_child():
            spk.remove(spk.get_child())
        img = ui.icon("volume_mute" if m else "volume", 20, "#8A8F98" if m else "#3F434B")
        img.show()
        spk.add(img)
        spk.set_tooltip_text("Unmute" if m else "Mute")
        scale.set_opacity(0.5 if m else 1.0)
        pct.set_text("Muted" if m else f"{int(scale.get_value())}%")

    def set_muted(m):
        state["muted"] = m
        sysinfo.mute_set(m)
        show_mute()

    def moved(s):
        sysinfo.volume_set(int(s.get_value()))
        if state["muted"]:
            set_muted(False)             # moving the slider unmutes, as on other systems
        else:
            pct.set_text(f"{int(s.get_value())}%")
    scale.connect("value-changed", moved)
    spk.connect("clicked", lambda *_: set_muted(not state["muted"]))
    show_mute()

    # Output device: its name, or a pick list when there's more than one
    sinks = sysinfo.audio_devices()["sinks"]
    if len(sinks) > 1:
        card.pack_start(Gtk.Separator(), False, False, 0)
        card.pack_start(ui.label("Output", "flyout-title"), False, False, 0)
        outs = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for s in sinks:
            b = ui.add_class(Gtk.Button(), "flyout-row")
            r = Gtk.Box(spacing=10)
            name = ui.label(s["name"], "body")
            name.set_ellipsize(3)
            r.pack_start(name, True, True, 0)
            if s["default"]:
                r.pack_start(ui.icon("check", 16, "#2F6FD6", 2), False, False, 0)
            b.add(r)
            b.connect("clicked", lambda *_, i=s["id"]: (sysinfo.audio_default(i),
                                                         app.toggle_flyout("volume", reopen=True)))
            outs.pack_start(b, False, False, 0)
        card.pack_start(outs, False, False, 0)
    else:
        name = sinks[0]["name"] if sinks else "No sound output found"
        card.pack_start(ui.label(name, "muted small"), False, False, 0)

    card.pack_start(_link("Sound settings", lambda: _admin(app, lambda: app.open_settings("sound"))),
                    False, False, 0)
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
    """The Settings launcher: a compact menu, every page with its icon, groups split by a line
    (no headings), Add connection last."""
    card = ui.add_class(Gtk.Box(orientation=Gtk.Orientation.VERTICAL), "card", "menu-card")
    card.set_size_request(214, -1)
    body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=5, margin_bottom=5,
                   margin_start=5, margin_end=5)
    from .dialogs import READY_PANELS, SETTINGS_GROUPS

    def item(icon, text, cb, cls="", colour="#3F434B", sensitive=True):
        b = ui.add_class(Gtk.Button(), "menu-row")
        row = Gtk.Box(spacing=10)
        row.pack_start(ui.icon(icon, 16, colour, 1.9), False, False, 0)
        row.pack_start(ui.label(text, cls), False, False, 0)
        b.add(row)
        b.connect("clicked", lambda *_: cb())
        b.set_sensitive(sensitive)
        body.pack_start(b, False, False, 0)

    for gi, (_title, items) in enumerate(SETTINGS_GROUPS):
        if gi:
            body.pack_start(ui.add_class(Gtk.Box(), "menu-line"), False, False, 0)
        for key, text, icon in items:
            item(icon, text, lambda k=key: app.open_settings(k), sensitive=key in READY_PANELS)
    # Last item: new connections are added here (the desktop only lists real ones)
    body.pack_start(ui.add_class(Gtk.Box(), "menu-line"), False, False, 0)
    item("plus", "Add connection", lambda: app.edit_connection(None), "add-link", "#2659B3")
    # 14 px above the card, 40 below it (shadow room)
    card.pack_start(_fit(body, 14 + 40 + 10), False, False, 0)
    return card, -14          # negative = anchored to the top (opened from the kite button)
