"""Settings panels: Sound, Keyboard & input, Connections and Diagnostics.

Each panel(settings, page) fills the page of the Settings window (settings is
the SettingsWindow: its list helpers, header and show())."""
import os
import time

from gi.repository import GLib, Gtk

from . import system, ui
from .config import address_label


def _section(page, title):
    page.pack_start(ui.add_class(ui.label(title, "group-title"), "section-title"), False, False, 0)


def _scale(lo, hi, step, value, width=240, on_change=None, on_release=None):
    s = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
    s.set_draw_value(False)
    s.set_value(value)
    s.set_size_request(width, -1)
    s.set_valign(Gtk.Align.CENTER)
    if on_change:
        s.connect("value-changed", lambda w: on_change(w.get_value()))
    if on_release:
        s.connect("button-release-event", lambda w, _e: (on_release(w.get_value()), False)[1])
        s.connect("key-release-event", lambda w, _e: (on_release(w.get_value()), False)[1])
    return s


def _status(lbl, text, kind):
    """Set a status label's text and colour (ok / err / faint)."""
    lbl.set_text(text)
    ctx = lbl.get_style_context()
    for c in ("ok", "err", "faint", "muted"):
        ctx.remove_class(c)
    ctx.add_class(kind)


def _gone(widget):
    return widget.get_ancestor(Gtk.Window) is None


# ============================================================== Sound
def sound(win, page):
    from .dialogs import combo
    win.set_header("Speakers, headsets and microphones. Remote sessions use these devices.")
    page.pack_start(ui.label("Looking for sound devices…", "faint body"), False, False, 0)

    def fill(res):
        if _gone(page):
            return
        devs, (vol, muted), (mic, mic_muted) = res
        for c in page.get_children()[1:]:
            page.remove(c)
        # -- Output -----------------------------------------------------
        _section(page, "OUTPUT")
        g = win._list_group(page)
        sinks = devs["sinks"]
        if not sinks:
            win._list_row(g, "No sound output found", "Connect speakers or a headset", None)
        else:
            if len(sinks) > 1:
                cur = next((d["id"] for d in sinks if d["default"]), sinks[0]["id"])
                pick = combo([(d["id"], d["name"]) for d in sinks], cur)
                pick.set_size_request(260, -1)
                pick.connect("changed", lambda c: (system.audio_default(c.get_active_id()),
                                                   GLib.timeout_add(400, lambda: (win.show("sound"), False)[1])))
                win._list_row(g, "Output device", None, pick)
            else:
                win._list_row(g, "Output device", None, win._value(sinks[0]["name"]))
            pct = ui.label(f"{vol}%", "muted body")
            pct.set_width_chars(5)
            box = Gtk.Box(spacing=10)
            box.pack_start(_scale(0, 100, 1, vol, on_change=lambda v: (system.volume_set(int(v)),
                                                                       pct.set_text(f"{int(v)}%"))),
                           False, False, 0)
            box.pack_start(pct, False, False, 0)
            win._list_row(g, "Volume", None, box)
            mute = Gtk.Switch(active=muted, valign=Gtk.Align.CENTER)
            mute.connect("notify::active", lambda s, *_: system.mute_set(s.get_active()))
            win._list_row(g, "Mute", None, mute)
            play = ui.button("Play", "btn")
            play.set_valign(Gtk.Align.CENTER)

            def test(*_):
                play.set_sensitive(False)
                system.in_background(system.play_test_sound, lambda _ok: play.set_sensitive(True))
            play.connect("clicked", test)
            win._list_row(g, "Test sound", "Plays a short chime on this output", play)

        # -- Input ------------------------------------------------------
        _section(page, "INPUT")
        g2 = win._list_group(page)
        sources = devs["sources"]
        if not sources:
            win._list_row(g2, "No microphone found", "Connect a headset or a USB microphone", None)
            return
        if len(sources) > 1:
            cur = next((d["id"] for d in sources if d["default"]), sources[0]["id"])
            pick = combo([(d["id"], d["name"]) for d in sources], cur)
            pick.set_size_request(260, -1)
            pick.connect("changed", lambda c: (system.audio_default(c.get_active_id()),
                                               GLib.timeout_add(400, lambda: (win.show("sound"), False)[1])))
            win._list_row(g2, "Input device", None, pick)
        else:
            win._list_row(g2, "Input device", None, win._value(sources[0]["name"]))
        mpct = ui.label(f"{mic}%", "muted body")
        mpct.set_width_chars(5)
        box = Gtk.Box(spacing=10)
        box.pack_start(_scale(0, 100, 1, mic, on_change=lambda v: (system.source_volume_set(int(v)),
                                                                   mpct.set_text(f"{int(v)}%"))),
                       False, False, 0)
        box.pack_start(mpct, False, False, 0)
        win._list_row(g2, "Microphone level", None, box)
        mm = Gtk.Switch(active=mic_muted, valign=Gtk.Align.CENTER)
        mm.connect("notify::active", lambda s, *_: system.source_mute_set(s.get_active()))
        win._list_row(g2, "Mute microphone", "Whether a remote session may use it is set per connection "
                                             "(Devices tab)", mm)
        page.show_all()

    def fill_and_show(res):
        fill(res)
        if not _gone(page):
            page.show_all()
    system.in_background(lambda: (system.audio_devices(), system.volume_get(), system.source_volume_get()),
                         fill_and_show)


# ============================================================== Keyboard & input
def keyboard(win, page):
    from .dialogs import combo
    cfg = win.app.cfg
    s = cfg.data.setdefault("input", {})
    win.set_header("Keyboard layout and how keys, mice and touchpads respond. Remote sessions use the "
                   "same layout.")
    layouts, variants = system.keyboard_layouts()
    cur_layout, cur_variant = system.keyboard_current()
    layout = s.get("layout") or cur_layout
    variant = s.get("variant") if s.get("layout") else cur_variant

    pending = {"id": 0}

    def save(apply=True):
        cfg.save()
        if apply:
            # Settle sliders first, then apply off the UI thread (xinput is slow-ish)
            if pending["id"]:
                GLib.source_remove(pending["id"])

            def go():
                pending["id"] = 0
                snapshot = dict(s)
                system.in_background(lambda: system.apply_input(snapshot), lambda _r: None)
                return False
            pending["id"] = GLib.timeout_add(250, go)

    # -- Keyboard ---------------------------------------------------------
    _section(page, "KEYBOARD")
    g = win._list_group(page)
    if layouts:
        lay = combo(layouts, layout)
        lay.set_size_request(260, -1)
        win._list_row(g, "Layout", None, lay)
        var = combo([("", "Default")] + variants.get(layout, []), variant or "")
        var.set_size_request(260, -1)
        win._list_row(g, "Variant", "For example with dead keys, or a different key arrangement", var,
                      enabled=bool(variants.get(layout)))

        def layout_changed(c):
            s["layout"], s["variant"] = c.get_active_id(), ""
            save()
            GLib.timeout_add(300, lambda: (win.show("keyboard"), False)[1])

        def variant_changed(c):
            s["layout"], s["variant"] = lay.get_active_id(), c.get_active_id() or ""
            save()
        lay.connect("changed", layout_changed)
        var.connect("changed", variant_changed)
    else:
        win._list_row(g, "Layout", "Keyboard layouts aren’t available on this system", win._value(layout))

    def setter(key, cast=int):
        def f(v):
            s[key] = cast(v)
            save()
        return f
    def with_value(scale, fmt):
        """A slider with its value written at its right (like Volume in Sound)."""
        box = Gtk.Box(spacing=10)
        val = ui.label(fmt(scale.get_value()), "muted body")
        val.set_width_chars(7)
        val.set_xalign(1.0)
        scale.connect("value-changed", lambda w: val.set_text(fmt(w.get_value())))
        box.pack_start(scale, False, False, 0)
        box.pack_start(val, False, False, 0)
        return box
    delay = _scale(150, 1000, 10, s.get("repeat_delay", 500), on_release=setter("repeat_delay"))
    win._list_row(g, "Repeat delay", "How long to hold a key before it repeats",
                  with_value(delay, lambda v: f"{int(v)} ms"))
    rate = _scale(10, 60, 1, s.get("repeat_rate", 25), on_release=setter("repeat_rate"))
    win._list_row(g, "Repeat speed", "How fast a held key repeats",
                  with_value(rate, lambda v: f"{int(v)} / s"))
    test = Gtk.Entry(placeholder_text="Type here to test")
    test.set_size_request(240, -1)
    test.set_valign(Gtk.Align.CENTER)
    win._list_row(g, "Try it", None, test)

    # -- Mouse & touchpad -------------------------------------------------
    _section(page, "MOUSE & TOUCHPAD")
    g2 = win._list_group(page)
    speed = _scale(-1, 1, 0.05, s.get("pointer_speed", 0.0), on_release=setter("pointer_speed", float))
    speed.add_mark(0, Gtk.PositionType.BOTTOM, None)
    win._list_row(g2, "Pointer speed", "Mice and touchpads",
                  with_value(speed, lambda v: "Normal" if abs(v) < 0.03 else ("Slower" if v < 0 else "Faster")))
    primary = combo([("left", "Left"), ("right", "Right")], "right" if s.get("left_handed") else "left")
    primary.set_size_request(180, -1)
    primary.connect("changed", lambda c: setter("left_handed", bool)(c.get_active_id() == "right"))
    win._list_row(g2, "Primary button", "Right swaps the buttons for left-handed use", primary)
    tap = Gtk.Switch(active=s.get("tap_to_click", True), valign=Gtk.Align.CENTER)
    nat = Gtk.Switch(active=s.get("natural_scroll", False), valign=Gtk.Align.CENTER)
    tap.connect("notify::active", lambda w, *_: setter("tap_to_click", bool)(w.get_active()))
    nat.connect("notify::active", lambda w, *_: setter("natural_scroll", bool)(w.get_active()))
    tap_row = win._list_row(g2, "Tap to click", "Touchpad", tap)
    nat_row = win._list_row(g2, "Natural scrolling", "Touchpad: content moves with your fingers", nat)

    def found(devs):
        if _gone(page):
            return
        has_pad = any(d["touchpad"] for d in devs)
        for r in (tap_row, nat_row):
            r.set_sensitive(has_pad)
            if not has_pad:
                r.set_tooltip_text("No touchpad found")
    tap_row.set_sensitive(False)
    nat_row.set_sensitive(False)
    system.in_background(system.pointer_devices, found)


# ============================================================== Connections
PROTOCOL_NAMES = {"rdp": "Remote Desktop (RDP)", "vnc": "VNC", "web": "Web kiosk"}


def connections(win, page):
    app, cfg = win.app, win.app.cfg
    add = ui.button("Add connection", "btn", lambda: app.edit_connection(None, on_close=refresh))
    win.set_header("Saved on this device. The desktop shows them in this order.", add)

    def refresh():
        if not _gone(page):
            win.show("remote")

    conns = cfg.connections
    if not conns:
        page.pack_start(ui.label("No connections yet.", "muted body"), False, False, 0)
        return
    kiosk = cfg.kiosk_conn() if cfg.mode == "kiosk" else None
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page.pack_start(box, False, False, 0)
    for i, c in enumerate(conns):
        row = ui.add_class(Gtk.Box(spacing=12), "device-row")
        if i < len(conns) - 1:
            ui.add_class(row, "device-row-sep")
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER)
        col.pack_start(ui.label(c["name"] or c["host"], "strong body"), False, False, 0)
        bits = [PROTOCOL_NAMES.get(c["protocol"], c["protocol"].upper()), address_label(c)]
        if kiosk and kiosk["id"] == c["id"]:
            bits.append("Kiosk")
        elif c.get("auto"):
            bits.append("Connects at startup")
        sub = ui.label(" · ".join(b for b in bits if b), "muted small")
        sub.set_ellipsize(3)
        col.pack_start(sub, False, False, 0)
        row.pack_start(col, True, True, 0)

        arrows = (("chev_up", "Move up", -1), ("chev_down", "Move down", 1)) if len(conns) > 1 else ()
        for icon_name, tip, delta in arrows:
            b = ui.add_class(Gtk.Button(valign=Gtk.Align.CENTER), "icon-btn")
            b.add(ui.icon(icon_name, 16, "#5A606B", 2))
            b.set_tooltip_text(tip)
            b.set_sensitive(0 <= i + delta < len(conns))
            b.connect("clicked", lambda _b, idx=i, d=delta: move(idx, d))
            row.pack_start(b, False, False, 0)
        edit = ui.button("Edit", "btn", lambda conn=c: app.edit_connection(conn, on_close=refresh))
        edit.set_valign(Gtk.Align.CENTER)
        row.pack_start(edit, False, False, 0)
        rm = ui.button("Remove", "btn", lambda conn=c: remove(conn))
        rm.set_valign(Gtk.Align.CENTER)
        row.pack_start(rm, False, False, 0)
        box.pack_start(row, False, False, 0)

    def move(idx, delta):
        lst = cfg.data["connections"]
        lst[idx], lst[idx + delta] = lst[idx + delta], lst[idx]
        cfg.save()
        app.refresh_desktop()
        win.show("remote")

    def remove(conn):
        from .dialogs import small_dialog
        outer, dlg = small_dialog(f"Remove {conn['name'] or conn['host']}?")
        msg = ui.label("The connection and its saved sign-in are removed from this device.", "muted body",
                       wrap=True)
        msg.set_max_width_chars(46)
        dlg.pack_start(msg, False, False, 0)
        row = Gtk.Box(spacing=10, halign=Gtk.Align.END)
        row.pack_start(ui.button("Cancel", "btn", app.pop_layer), False, False, 0)

        def go():
            cfg.delete(conn["id"])
            if cfg.data.get("kiosk_connection") == conn["id"]:
                cfg.data["kiosk_connection"] = ""
                cfg.save()
            app.refresh_desktop()
            app.pop_layer()
            win.show("remote")
        row.pack_start(ui.button("Remove", "btn btn-red", go), False, False, 0)
        dlg.pack_start(row, False, False, 0)
        app.push_layer(outer, "light")


# ============================================================== Diagnostics
def _conn_target(c):
    """-> (host, port) a connection reaches first."""
    from urllib.parse import urlsplit
    host = (c.get("host") or "").strip()
    if c.get("protocol") == "web":
        u = urlsplit(host if "://" in host else "https://" + host)
        port = str(c.get("web", {}).get("port") or "")
        return u.hostname or host, int(port) if port.isdigit() else (u.port or (80 if u.scheme == "http" else 443))
    if c.get("gateway"):
        g = c["gateway"].strip()
        h, _, p = g.rpartition(":") if g.count(":") == 1 else (g, "", "")
        return (h or g), int(p) if p.isdigit() else 443
    if host.count(":") == 1:
        h, p = host.split(":")
        return h, int(p) if p.isdigit() else 3389
    return host, 3389


def diagnostics(win, page):
    app = win.app
    save = ui.button("Save report to USB", "btn", lambda: save_report(win, results))
    win.set_header("Checks for when a connection doesn’t work, and a report to send to support.", save)
    results = []           # (check, ok, detail) of the last test, for the report

    # -- Network test -----------------------------------------------------
    head = Gtk.Box(spacing=12)
    head.pack_start(ui.add_class(ui.label("NETWORK TEST", "group-title"), "section-title"), True, True, 0)
    again = ui.add_class(Gtk.Button(label="Run again"), "link-btn")
    again.set_valign(Gtk.Align.CENTER)
    head.pack_start(again, False, False, 0)
    page.pack_start(head, False, False, 0)
    g = win._list_group(page)
    checks = [("network", "Network", "Connected to a network"),
              ("gateway", "Router", "The gateway answers"),
              ("dns", "Name lookup (DNS)", "Names like deb.debian.org resolve"),
              ("internet", "Internet", "A secure web connection to deb.debian.org")]
    for c in app.cfg.connections:
        h, p = _conn_target(c)
        checks.append((f"conn:{c['id']}", c["name"] or h, f"Reaches {h} on port {p}"))
    labels = {}
    for key, title, sub in checks:
        st = ui.label("", "faint small")
        st.set_ellipsize(3)
        st.set_max_width_chars(36)
        win._list_row(g, title, sub, st)
        labels[key] = (title, st)

    def run_test(*_):
        again.set_sensitive(False)
        results.clear()
        for _t, st in labels.values():
            _status(st, "Waiting…", "faint")

        def report(key, ok, detail):
            title, st = labels[key]
            results.append((title, ok, detail))
            if not _gone(st):
                _status(st, detail, "ok" if ok else ("err" if ok is False else "faint"))

        def work():
            net = system.network_status()
            on = net["state"] == "connected"
            system.on_main(report, "network", on,
                           f"{net['name']} · {net['ip']}" if on and net.get("ip") else
                           (net["name"] if on else "Not connected"))
            gw, _dns = system.gateway_dns()
            if not gw:
                system.on_main(report, "gateway", False, "No gateway")
            else:
                ms = system.ping(gw)
                # Some routers ignore pings: not an error on its own
                if ms is False:
                    system.on_main(report, "gateway", None, f"{gw} · can’t test here")
                elif ms is None:
                    system.on_main(report, "gateway", None, f"{gw} doesn’t answer pings")
                else:
                    system.on_main(report, "gateway", True, f"{gw} · {ms} ms")
            ok, addr = system.resolve("deb.debian.org")
            system.on_main(report, "dns", ok, f"Works · {addr}" if ok else "Names don’t resolve")
            ok, v = system.tcp_check("deb.debian.org", 443)
            system.on_main(report, "internet", ok, f"Works · {v} ms" if ok else f"Failed · {v}")
            for c in app.cfg.connections:
                h, p = _conn_target(c)
                if not h:
                    system.on_main(report, f"conn:{c['id']}", False, "No address set")
                    continue
                ok, v = system.tcp_check(h, p)
                system.on_main(report, f"conn:{c['id']}", ok, f"Reachable · {v} ms" if ok else f"Failed · {v}")
        system.in_background(work, lambda _r: again.set_sensitive(True))
    again.connect("clicked", run_test)

    # -- Device -----------------------------------------------------------
    _section(page, "DEVICE")
    g2 = win._list_group(page)
    for k, v in system.device_health():
        win._list_row(g2, k, None, win._value(v))

    # -- Log --------------------------------------------------------------
    from .storage import LogView
    log = LogView([], open_=False)
    log.set_halign(Gtk.Align.FILL)
    # The Show / Hide details toggle sits at the right of the section title
    log.remove(log.toggle)
    lhead = Gtk.Box(spacing=12)
    lhead.pack_start(ui.add_class(ui.label("SYSTEM LOG", "group-title"), "section-title"), True, True, 0)
    log.toggle.set_valign(Gtk.Align.CENTER)
    lhead.pack_start(log.toggle, False, False, 0)
    page.pack_start(lhead, False, False, 0)
    page.pack_start(log, False, False, 0)

    def got_log(res):
        journal, shell = res
        if _gone(log):
            return
        text = (journal or "The system log isn’t available.").splitlines()
        if shell.strip():
            text += ["", "--- LiteLink shell ---"] + shell.rstrip().splitlines()
        log.lines = text[-400:]
        log.buf.set_text("\n".join(log.lines))
    system.in_background(lambda: system.recent_log(200), got_log)
    GLib.idle_add(lambda: (run_test(), False)[1])


def _report_text(app, results):
    info = system.about()
    lines = [f"LiteLink diagnostics report · {time.strftime('%Y-%m-%d %H:%M:%S %Z')}", ""]
    lines += [f"{k}: {v}" for k, v in info["rows"]]
    lines += ["", "== Device"] + [f"{k}: {v}" for k, v in system.device_health()]
    lines += ["", "== Network test"]
    lines += [f"{'OK  ' if ok else ('FAIL' if ok is False else '??  ')} {t}: {d}" for t, ok, d in results] \
        or ["(not run)"]
    lines += ["", "== Connections (no passwords)"]
    for c in app.cfg.connections:
        lines.append(f"{c['name']}: {c['protocol']} {c['host']}" + (f" via {c['gateway']}" if c.get("gateway") else "")
                     + (" auto" if c.get("auto") else ""))
    for title, cmd in (("Addresses", ["ip", "-br", "addr"]), ("Routes", ["ip", "route"]),
                       ("NetworkManager", ["nmcli", "-t", "device"]), ("USB devices", ["lsusb"]),
                       ("PCI devices", ["lspci"]), ("Displays", ["xrandr", "--query"])):
        lines += ["", f"== {title}", system.run(cmd, timeout=8)[1]]
    try:
        with open("/etc/resolv.conf") as f:
            lines += ["", "== resolv.conf", f.read().strip()]
    except OSError:
        pass
    journal, shell = system.recent_log(400)
    lines += ["", "== System log (this boot)", journal, "", "== LiteLink shell log", shell]
    return "\n".join(lines) + "\n"


def save_report(win, results):
    app = win.app
    from .dialogs import small_dialog
    outer, dlg = small_dialog("Save report to USB")
    outer.set_size_request(460, -1)
    msg = ui.label("Looking for USB drives…", "muted body", wrap=True)
    msg.set_max_width_chars(48)
    dlg.pack_start(msg, False, False, 0)
    drives = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    dlg.pack_start(drives, False, False, 0)
    row = Gtk.Box(spacing=10, halign=Gtk.Align.END)
    close = ui.button("Close", "btn", app.pop_layer)
    row.pack_start(close, False, False, 0)
    dlg.pack_start(row, False, False, 0)
    app.push_layer(outer, "light")

    def write(d):
        name = time.strftime("litelink-report-%Y%m%d-%H%M.txt")
        msg.set_text("Saving…")
        drives.set_sensitive(False)

        def done(res):
            ok, err = res
            drives.set_sensitive(True)
            if _gone(msg):
                return
            if ok:
                for c in drives.get_children():
                    drives.remove(c)
                msg.set_text(f"Saved {name} to {d['label'] or d['drive']}. You can remove the drive.")
            else:
                msg.set_markup(f'<span foreground="#C53030">Couldn’t save the report: '
                               f'{GLib.markup_escape_text(err or "unknown error")}</span>')
        system.in_background(lambda: system.save_to_usb(d["path"], name, _report_text(app, results)), done)

    def listed(found):
        if _gone(msg):
            return
        if not found:
            msg.set_text("Plug in a USB drive (FAT32, exFAT or NTFS), then open this again.")
            return
        msg.set_text("Choose the drive for the report. It has no passwords in it.")
        for i, d in enumerate(found):
            r = ui.add_class(Gtk.Box(spacing=12), "device-row")
            if i < len(found) - 1:
                ui.add_class(r, "device-row-sep")
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            col.pack_start(ui.label(d["label"] or d["drive"], "body"), False, False, 0)
            col.pack_start(ui.label(f"{d['drive']} · {system.human_size(d['size'])} · {d['fstype']}",
                                    "faint small"), False, False, 0)
            r.pack_start(col, True, True, 0)
            b = ui.button("Save here", "btn btn-primary", lambda dd=d: write(dd))
            b.set_valign(Gtk.Align.CENTER)
            r.pack_start(b, False, False, 0)
            drives.pack_start(r, False, False, 0)
        drives.show_all()
    system.in_background(system.media_list, listed)


# ============================================================== Certificates
def certificates(win, page):
    """Settings › Certificates: certificate authorities of the organisation's own
    (a LiteOS server on the LAN with its own certificate, a company CA). Web
    sessions and the device bridge trust sites they signed; the list is kept with
    the settings and applied at every start."""
    app = win.app
    add = ui.button("Add certificate", "btn", lambda: pick())
    win.set_header("Certificate authorities this device trusts besides the usual public ones, for web "
                   "connections such as a LiteOS server on your network.", add)
    status = ui.label("", "small")
    error, win._certs_error = getattr(win, "_certs_error", ""), ""
    if error:
        status.set_markup('<span foreground="#C53030">%s</span>' % GLib.markup_escape_text(error))
    certs = system.ca_list()
    if not certs:
        from .vpn import empty_state
        page.pack_start(empty_state("shield", "No certificates",
                                    "Add the certificate file from your IT team, from a USB drive "
                                    "(.pem, .crt, .cer or .der)."), True, True, 0)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page.pack_start(box, False, False, 0)
    buttons = [add]
    for i, c in enumerate(certs):
        row = ui.add_class(Gtk.Box(spacing=12), "device-row")
        if i < len(certs) - 1:
            ui.add_class(row, "device-row-sep")
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER)
        col.pack_start(ui.label(c["subject"] or "Unnamed certificate", "strong body"), False, False, 0)
        bits = []
        if c["issuer"] and c["issuer"] != c["subject"]:
            bits.append(f"Issued by {c['issuer']}")
        if c["expires"]:
            bits.append(f"Expires {' '.join(c['expires'].split()[:2] + c['expires'].split()[3:4])}")
        sub = ui.label(" · ".join(bits) or "Certificate authority", "muted small")
        sub.set_ellipsize(3)
        sub.set_tooltip_text("SHA-256 " + ":".join(c["fingerprint"][j:j + 2] for j in range(0, 64, 2)).upper())
        col.pack_start(sub, False, False, 0)
        row.pack_start(col, True, True, 0)
        rm = ui.button("Remove", "btn")
        rm.connect("clicked", lambda b, fp=c["fingerprint"]: remove(fp, b))
        rm.set_valign(Gtk.Align.CENTER)
        row.pack_start(rm, False, False, 0)
        buttons.append(rm)
        box.pack_start(row, False, False, 0)
    page.pack_start(status, False, False, 0)

    def busy(button, text):
        """Applying takes several seconds (the device's certificate stores are rebuilt): the button that
        started it says so with a spinner, and nothing else can be started until the page refreshes."""
        for b in buttons:
            b.set_sensitive(False)
        button.set_size_request(button.get_allocated_width(), -1)   # same width: nothing beside it moves
        inner = button.get_child()
        for ch in inner.get_children():
            inner.remove(ch)
        spin = Gtk.Spinner(active=True)
        spin.set_size_request(14, 14)
        inner.pack_start(spin, False, False, 0)
        inner.pack_start(Gtk.Label(label=text), False, False, 0)
        inner.show_all()

    def done(ok, message=""):
        if _gone(page):
            return
        # the list always shows what's kept; a failure is said on the refreshed page
        win._certs_error = "" if ok else (message or "The certificates couldn\u2019t be applied to this device.")
        win.show("certs")

    def pick():
        def chosen(path):
            def work():
                ok, message = system.ca_add(path)
                try:
                    os.remove(path)         # the copy the picker made; the certificate is kept as PEM
                except OSError:
                    pass
                return ok, message
            busy(add, "Adding…")
            system.in_background(work, lambda r: done(*r))
        win._pick_cert(chosen, suffixes=(".pem", ".crt", ".cer", ".der"))

    def remove(fp, button):
        busy(button, "Removing…")
        system.in_background(lambda: system.ca_remove(fp), lambda ok: done(ok))


# ============================================================== Printers
def printers(win, page):
    """Settings › Printers (print image module): the printers this device prints
    to, for LiteOS sessions where the connection allows printing. Driverless
    printers only (IPP Everywhere / AirPrint), over USB or the network; CUPS
    describes each one from the printer itself, so nothing is installed for it."""
    app = win.app
    add = ui.button("Add printer", "btn", lambda: add_dialog())
    win.set_header("Printers this device can print to, plugged in by USB or on your network.", add)
    status = ui.label("", "small")
    note, win._printers_note = getattr(win, "_printers_note", ("", True)), ("", True)
    if note[0]:
        colour = "#5A606B" if note[1] else "#C53030"
        status.set_markup('<span foreground="%s">%s</span>' % (colour, GLib.markup_escape_text(note[0])))
    found = system.printers()
    if not found:
        from .vpn import empty_state
        page.pack_start(empty_state("printer", "No printers yet",
                                    "Add a printer that’s plugged into this device or on your network."),
                        True, True, 0)
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    page.pack_start(box, False, False, 0)
    for i, p in enumerate(found):
        row = ui.add_class(Gtk.Box(spacing=14), "device-row")
        if i < len(found) - 1:
            ui.add_class(row, "device-row-sep")
        glyph = ui.icon("printer", 22, "#5A606B", 1.8)
        glyph.set_valign(Gtk.Align.CENTER)
        row.pack_start(glyph, False, False, 0)
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER)
        col.pack_start(ui.label(p["info"], "strong body"), False, False, 0)
        state = {"idle": "Ready", "printing": "Printing", "stopped": "Stopped"}[p["state"]]
        if p["state"] == "stopped" and p["message"]:
            state += ": " + p["message"]
        bits = ["USB" if p["kind"] == "usb" else "Network", state] + (["Default"] if p["default"] else [])
        sub = ui.label(" · ".join(bits), "muted small")
        sub.set_ellipsize(3)
        sub.set_tooltip_text(p["uri"])
        col.pack_start(sub, False, False, 0)
        row.pack_start(col, True, True, 0)
        # icon actions; no default button: the printer used last is the default
        acts = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        for icon, colour, tip, cb in (("testpage", "#3F434B", "Print test page", lambda n=p["name"]: test(n)),
                                      ("trash", "#C53030", "Remove",
                                       lambda n=p["name"]: act(lambda: system.printer_remove(n)))):
            b = ui.add_class(Gtk.Button(), "icon-btn", "row-icon-btn")
            b.set_size_request(36, 36)
            b.add(ui.icon(icon, 18, colour, 1.8))
            b.set_tooltip_text(tip)
            b.get_accessible().set_name(tip)
            b.connect("clicked", lambda _b, c=cb: c())
            acts.pack_start(b, False, False, 0)
        row.pack_start(acts, False, False, 0)
        box.pack_start(row, False, False, 0)
    page.pack_start(status, False, False, 0)

    def refresh(message="", good=True):
        if _gone(page):
            return
        win._printers_note = (message, good)
        win.show("printers")

    def act(work):
        system.in_background(work, lambda r: refresh("" if r[0] else r[1], r[0]))

    def test(name):
        system.in_background(lambda: system.printer_test(name), lambda r: refresh("" if r[0] else r[1], r[0]))

    def add_dialog():
        outer, dlg = win._small_dialog("Add a printer")
        outer.set_size_request(500, -1)
        hint = ui.label("Turn the printer on and connect it to this device or your network. Printers that work "
                        "without a driver (most printers made since 2015) can be added.", "muted small", wrap=True)
        dlg.pack_start(hint, False, False, 0)
        listing = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        listing.set_size_request(-1, 150)

        def state(title, text, busy=False):
            """The list's empty state: searching (a spinner) or nothing found, centred in the list."""
            for ch in listing.get_children():
                listing.remove(ch)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER,
                          valign=Gtk.Align.CENTER)
            if busy:
                spin = Gtk.Spinner(active=True)
                spin.set_size_request(28, 28)
                lead = spin
            else:
                lead = ui.icon("printer", 40, "#B8BEC8", 1.4)
            lead.set_margin_bottom(4)
            box.pack_start(lead, False, False, 0)
            box.pack_start(ui.label(title, "strong body", xalign=0.5), False, False, 0)
            if text:
                sub = ui.label(text, "muted small", xalign=0.5, wrap=True)
                sub.set_max_width_chars(52)
                sub.set_justify(Gtk.Justification.CENTER)
                box.pack_start(sub, False, False, 0)
            listing.pack_start(box, True, True, 0)
            listing.show_all()
        dlg.pack_start(listing, False, False, 0)
        dlg.pack_start(ui.label("Or add it by address", "field-label"), False, False, 0)
        by = Gtk.Box(spacing=8)
        addr = Gtk.Entry(placeholder_text="ipp://printer-name.local or ipps://192.168.1.50")
        by.pack_start(addr, True, True, 0)
        by.pack_start(ui.button("Add", "btn", lambda: add_uri(addr.get_text().strip(), "")), False, False, 0)
        dlg.pack_start(by, False, False, 0)
        msg = ui.label("", "small", wrap=True)
        dlg.pack_start(msg, False, False, 0)
        again = ui.button("Search again", "btn", lambda: search())
        foot = Gtk.Box(spacing=10)
        foot.pack_start(again, False, False, 0)
        foot.pack_start(Gtk.Box(), True, True, 0)
        foot.pack_start(ui.button("Close", "btn", app.pop_layer), False, False, 0)
        dlg.pack_start(foot, False, False, 0)
        addr.connect("activate", lambda *_: add_uri(addr.get_text().strip(), ""))

        def say(text, bad=False):
            msg.set_markup('<span foreground="%s">%s</span>' % ("#C53030" if bad else "#5A606B",
                                                                 GLib.markup_escape_text(text)))

        def add_uri(uri, title):
            if not uri:
                return
            if "://" not in uri:
                uri = "ipp://" + uri + "/ipp/print"
            taken = {p["name"] for p in system.printers()}
            label = title or uri.split("://", 1)[1].split("/", 1)[0]
            name = system.printer_name(label, taken)
            listing.set_sensitive(False)
            by.set_sensitive(False)
            say(f"Adding {label}…")

            def done(res):
                ok, message = res
                if _gone(dlg):
                    return
                listing.set_sensitive(True)
                by.set_sensitive(True)
                if ok:
                    app.pop_layer()
                    refresh()
                else:
                    say(message, bad=True)
            system.in_background(lambda: system.printer_add(name, uri, title), done)

        def show_found(res):
            if _gone(dlg):
                return
            ok, items = res
            again.set_sensitive(True)
            if not ok:
                state("Couldn’t look for printers", items)
                return
            if not items:
                state("No printers found", "Check that the printer is on and connected, or add it by its address.")
                return
            for ch in listing.get_children():
                listing.remove(ch)
            here = system.printers()
            # the same address, or the same name (one added by its address is found under its network name)
            have = {p["uri"] for p in here} | {p["info"] for p in here}
            for f in items[:8]:
                r = Gtk.Box(spacing=12)
                col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
                col.pack_start(ui.label(f["name"], "body"), False, False, 0)
                if f["uri"] in have or f["name"] in have:
                    why = "Already added"
                elif f["driverless"]:
                    why = "USB" if f["kind"] == "usb" else "Network"
                else:
                    why = "Needs a driver LiteLink doesn’t include"
                col.pack_start(ui.label(why, "muted small"), False, False, 0)
                r.pack_start(col, True, True, 0)
                b = ui.button("Add", "btn", lambda u=f["uri"], n=f["name"]: add_uri(u, n))
                b.set_valign(Gtk.Align.CENTER)
                b.set_sensitive(f["driverless"] and f["uri"] not in have and f["name"] not in have)
                r.pack_start(b, False, False, 0)
                listing.pack_start(r, False, False, 0)
            listing.show_all()

        def search():
            again.set_sensitive(False)
            say("")
            state("Looking for printers…", "", busy=True)
            system.in_background(system.printer_find, show_found)

        app.push_layer(outer, "light")
        search()
