"""Full-screen system screens on the dark overlay: power, lock, connecting, errors."""
import time

from gi.repository import GLib, Gtk

from . import system, ui

# Severity colours: blue (low) → amber → orange → red (high)
SEVERITY = {"blue": "#2F6FD6", "amber": "#F5A524", "orange": "#E8710A", "red": "#E5484D"}


def _column(spacing=12):
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, halign=Gtk.Align.CENTER,
                   valign=Gtk.Align.CENTER)


def _kite_badge(badge_color, badge_icon, badge_fg="#FFFFFF"):
    ov = Gtk.Overlay(halign=Gtk.Align.CENTER)
    k = ui.kite(120, "#F2F5FA", 0.3)
    ov.add(k)
    badge = ui.add_class(Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.END), "badge", f"badge-{badge_color}")
    badge.pack_start(ui.icon(badge_icon, 18, badge_fg, 2.4), True, True, 0)
    ov.add_overlay(badge)
    ov.set_margin_bottom(18)
    return ov


def _center(text, cls):
    lb = ui.label(text, cls, xalign=0.5, wrap=True)
    lb.set_halign(Gtk.Align.CENTER)
    return lb


# ================================================================ power
def power_menu(app):
    col = _column(8)
    col.pack_start(_center("Power options", "h1"), False, False, 0)
    col.pack_start(_center("What do you want to do?", "sub"), False, False, 0)
    row = Gtk.Box(spacing=40, halign=Gtk.Align.CENTER, margin_top=30)

    def action(text, icon_name, color, fg, cb):
        b = ui.add_class(Gtk.Button(), "power-btn")
        v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        circle = ui.add_class(Gtk.Box(halign=Gtk.Align.CENTER), "power-circle")
        circle.override_background_color(Gtk.StateFlags.NORMAL, _rgba(color))
        circle.pack_start(ui.icon(icon_name, 26, fg, 1.9), True, True, 0)
        v.pack_start(circle, False, False, 0)
        v.pack_start(ui.label(text, xalign=0.5), False, False, 0)
        b.add(v)
        b.set_size_request(96, -1)
        b.connect("clicked", lambda *_: cb())
        row.pack_start(b, False, False, 0)

    action("Lock", "lock", SEVERITY["blue"], "#FFFFFF", app.lock)
    action("Restart", "restart", SEVERITY["orange"], "#FFFFFF", lambda: confirm(app, "reboot"))
    action("Shut down", "power", SEVERITY["red"], "#FFFFFF", lambda: confirm(app, "poweroff"))
    col.pack_start(row, False, False, 0)
    hint = _center("Press Esc to cancel", "hint")
    hint.set_margin_top(36)
    col.pack_start(hint, False, False, 0)
    return col


def _rgba(hex_):
    from gi.repository import Gdk
    c = Gdk.RGBA()
    c.parse(hex_)
    return c


CONFIRM = {
    "poweroff": dict(title="Shut down this device?", body="Any open remote session is closed first.",
                     verb="Shutting down", button="Shut down", color="red", icon="power"),
    "reboot": dict(title="Restart this device?",
                   body="Any open remote session is closed first. LiteLink starts again in about a minute.",
                   verb="Restarting", button="Restart", color="orange", icon="restart"),
}


def confirm(app, action, seconds=30):
    app.pop_layer()          # the power menu; anything below (e.g. the lock screen) stays
    spec = CONFIRM[action]
    col = _column()
    col.pack_start(_kite_badge(spec["color"], spec["icon"]), False, False, 0)
    col.pack_start(_center(spec["title"], "h1"), False, False, 0)
    col.pack_start(_center(spec["body"], "sub"), False, False, 0)
    state = {"left": seconds, "timer": None}

    def go():
        if state["timer"]:
            GLib.source_remove(state["timer"])
            state["timer"] = None
        app.cancel_session()
        system.power(action)

    def tick():
        count.set_text(str(state["left"]))
        if state["left"] <= 0:
            state["timer"] = None
            go()
            return False
        state["left"] -= 1
        return True

    def stop():
        if state["timer"]:
            GLib.source_remove(state["timer"])
            state["timer"] = None

    row = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, margin_top=22)
    primary, count = ui.count_button(spec["button"], f"btn pill btn-{spec['color']}", go, spec["icon"])
    primary.set_tooltip_text(f"{spec['verb']} automatically when the count reaches 0")
    row.pack_start(primary, False, False, 0)
    row.pack_start(ui.button("Cancel", "btn pill btn-ghost", lambda: app.pop_layer()), False, False, 0)
    col.pack_start(row, False, False, 0)
    tick()
    state["timer"] = GLib.timeout_add_seconds(1, tick)
    app.push_layer(col, "dark", on_close=stop)


# ================================================================ lock
def lock_screen(app):
    box = Gtk.Overlay()
    center = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER,
                     valign=Gtk.Align.START, margin_top=150)
    from .config import fmt_time
    clock = ui.label(fmt_time(app.cfg.data, time.localtime()), "lock-clock", xalign=0.5)
    app.lock_clock = clock
    center.pack_start(clock, False, False, 0)
    center.pack_start(ui.label(time.strftime("%A, %-d %B"), "lock-date", xalign=0.5), False, False, 0)

    form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14, halign=Gtk.Align.CENTER, margin_top=90)
    form.set_size_request(300, -1)
    chip = ui.add_class(Gtk.Box(halign=Gtk.Align.CENTER), "round-glass")
    chip.pack_start(ui.icon("lock", 20, "#FFFFFF"), True, True, 0)
    form.pack_start(chip, False, False, 0)
    form.pack_start(ui.label("This device is locked", "strong body", xalign=0.5), False, False, 0)
    entry_holder = {}

    def try_unlock(*_):
        pw = entry_holder["e"].get_text() if "e" in entry_holder else ""
        if app.cfg.check_lock_password(pw):
            app.unlock(app.layers[-1])
        else:
            entry_holder["e"].set_text("")
            entry_holder["e"].set_placeholder_text("Wrong password. Try again")

    if app.cfg.lock_needs_password():
        ov = Gtk.Overlay()
        e = Gtk.Entry(placeholder_text="Enter password to unlock", visibility=False)
        e.connect("activate", try_unlock)
        entry_holder["e"] = e
        ov.add(e)
        go = ui.add_class(Gtk.Button(halign=Gtk.Align.END, valign=Gtk.Align.CENTER, margin_end=4), "round-blue")
        go.add(ui.icon("arrow", 18, "#FFFFFF", 2.2))
        go.connect("clicked", try_unlock)
        ov.add_overlay(go)
        form.pack_start(ov, False, False, 0)
        GLib.idle_add(e.grab_focus)
    else:
        form.pack_start(ui.button("Unlock", "btn pill btn-primary", try_unlock), False, False, 0)
    center.pack_start(form, False, False, 0)
    box.add(center)

    corner = Gtk.Box(spacing=8, halign=Gtk.Align.START, valign=Gtk.Align.END, margin_start=28, margin_bottom=26)
    pb = ui.add_class(Gtk.Button(), "round-glass")
    pb.add(ui.icon("power", 18, "#FFFFFF", 1.9))
    pb.set_tooltip_text("Power options")
    pb.connect("clicked", lambda *_: app.push_layer(power_menu(app), "dark"))
    corner.pack_start(pb, False, False, 0)
    st = system.network_status()
    net = ui.add_class(Gtk.Box(spacing=8), "round-glass")
    net.set_margin_top(0)
    lab = ui.label(st["name"], "small")
    lab.set_margin_end(14)
    net_icon = ui.icon("network", 16, "#FFFFFF", 1.9)
    net_icon.set_margin_start(12)
    net.pack_start(net_icon, False, False, 0)
    net.pack_start(lab, False, False, 0)
    corner.pack_start(net, False, False, 0)
    box.add_overlay(corner)

    logo = ui.picture("logo-light.png", 112)
    logo.set_halign(Gtk.Align.END)
    logo.set_valign(Gtk.Align.END)
    logo.set_margin_end(28)
    logo.set_margin_bottom(24)
    box.add_overlay(logo)
    return box


# ================================================================ sessions
def connecting(app, conn):
    col = _column(14)
    anim = ui.FrameAnimation("connect/connect-*.png")
    anim.set_margin_bottom(18)
    col.pack_start(anim, False, False, 0)
    col.pack_start(_center(f"Connecting to {conn['name']}", "h1"), False, False, 0)
    kind = {"web": "Web kiosk"}.get(conn.get("protocol"), "Remote Desktop")
    col.pack_start(_center(f"{conn['host']} · {kind}", "sub"), False, False, 0)
    cancel = ui.button("Cancel", "btn pill btn-ghost", app.cancel_session)
    cancel.set_halign(Gtk.Align.CENTER)
    cancel.set_margin_top(22)
    col.pack_start(cancel, False, False, 0)
    return col


ERRORS = {
    # Not an error: asked before connecting when no username or password is saved
    "signin": dict(title="Sign in to {name}", body="Enter your account for {host}.",
                   color="blue", icon="lock", fg="#FFFFFF"),
    "noengine": dict(title="Web kiosk isn’t installed",
                     body="This device was installed without the browser engine, so {name} can’t open. "
                          "Reinstall from a LiteLink USB stick with Web kiosk ticked.",
                     color="amber", icon="warn", fg="#2B1A00"),
    "unreachable": dict(title="Couldn’t connect to {name}",
                        body="{host} didn’t respond. Check that the computer is on and connected to the network, then try again.",
                        color="amber", icon="warn", fg="#2B1A00"),
    "auth": dict(title="Sign-in failed",
                 body="The username or password for {name} wasn’t accepted. Enter them again.",
                 color="red", icon="lock", fg="#FFFFFF"),
    "certificate": dict(title="Can’t verify {name}",
                        body="The remote computer’s certificate isn’t trusted or has changed. Only continue if you’re sure this is the right computer.",
                        color="amber", icon="shield", fg="#2B1A00"),
    "ended": dict(title="Session ended", body="The connection to {name} was lost.",
                  color="blue", icon="reconnect", fg="#FFFFFF"),
    "vpn": dict(title="Couldn’t connect the VPN",
                body="{name} is reached through the {vpn_name} tunnel, which couldn’t be connected. "
                     "Check the network connection, then try again.",
                color="amber", icon="vpn", fg="#2B1A00"),
}


def error(app, conn, kind, code, details, extra=None):
    """Full-screen result of a connection attempt. In kiosk mode there's no
    desktop to go back to and nothing to edit: errors retry by themselves and
    Back returns to the kiosk screen."""
    kiosk = getattr(app, "kiosk", None) is not None
    back_label = "Back" if kiosk else "Back to desktop"
    back = (lambda: app.kiosk_home(autoconnect=False)) if kiosk else app.pop_layer
    spec = ERRORS.get(kind, ERRORS["unreachable"])
    fmt = dict(conn, **(extra or {}))
    col = _column(12)
    col.set_size_request(520, -1)
    col.pack_start(_kite_badge(spec["color"], spec["icon"], spec["fg"]), False, False, 0)
    col.pack_start(_center(spec["title"].format(**fmt), "h1"), False, False, 0)
    col.pack_start(_center(spec["body"].format(**fmt), "sub"), False, False, 0)
    buttons = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER, margin_top=22)
    state = {"timer": None}

    def stop():
        if state["timer"]:
            GLib.source_remove(state["timer"])
            state["timer"] = None

    if kind in ("auth", "signin"):
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, halign=Gtk.Align.CENTER, margin_top=10)
        form.set_size_request(360, -1)
        from .dialogs import field
        user = Gtk.Entry(text=conn.get("username", ""))
        pw = Gtk.Entry(placeholder_text="Password", visibility=False)
        form.pack_start(field("Username", user), False, False, 0)
        form.pack_start(field("Password", pw), False, False, 0)
        col.pack_start(form, False, False, 0)
        signin = lambda *_: app.start_session(conn, password=pw.get_text(), username=user.get_text().strip())
        pw.connect("activate", signin)
        user.connect("activate", lambda *_: pw.grab_focus())
        buttons.pack_start(ui.button("Connect" if kind == "signin" else "Sign in", "btn pill btn-primary", signin),
                           False, False, 0)
        buttons.pack_start(ui.button("Cancel" if kind == "signin" else back_label, "btn pill btn-ghost", back),
                           False, False, 0)
        GLib.idle_add((pw if user.get_text() else user).grab_focus)
    elif kind == "certificate":
        remember = Gtk.CheckButton(label="Remember this certificate", halign=Gtk.Align.CENTER)
        col.pack_start(remember, False, False, 0)

        def anyway():
            if remember.get_active():
                conn["ignore_cert"] = True
                app.save_connection(conn)
            app.start_session(conn, ignore_cert=True)
        buttons.pack_start(ui.button("Cancel", "btn pill btn-primary", back), False, False, 0)
        buttons.pack_start(ui.button("Connect anyway", "btn pill btn-ghost", anyway), False, False, 0)
    elif kind == "ended":
        reconnect = lambda: app.start_session(conn, **(extra or {}).get("creds", {}))
        if conn.get("reconnect"):
            # The countdown rides in the button: "Reconnect now | 5"
            btn, count = ui.count_button("Reconnect now", "btn pill btn-primary", reconnect)
            buttons.pack_start(btn, False, False, 0)
            left = {"n": 5}

            def tick():
                if left["n"] <= 0:
                    state["timer"] = None
                    reconnect()
                    return False
                count.set_text(str(left["n"]))
                left["n"] -= 1
                return True
            tick()
            state["timer"] = GLib.timeout_add_seconds(1, tick)
        else:
            buttons.pack_start(ui.button("Reconnect now", "btn pill btn-primary", reconnect), False, False, 0)
        buttons.pack_start(ui.button(back_label, "btn pill btn-ghost", back), False, False, 0)
    elif kind == "noengine":
        # Nothing to retry: only a reinstall with Web kiosk ticked helps
        buttons.pack_start(ui.button(back_label, "btn pill btn-primary", back), False, False, 0)
    elif kiosk:
        # Unattended: try again by itself ("Try again | 30"), or go back to the kiosk screen
        retry = lambda: app.start_session(conn, **(extra or {}).get("creds", {}))
        btn, count = ui.count_button("Try again", "btn pill btn-primary", retry)
        btn.set_tooltip_text("Tries again automatically when the count reaches 0")
        buttons.pack_start(btn, False, False, 0)
        left = {"n": 30}

        def tick():
            if left["n"] <= 0:
                state["timer"] = None
                retry()
                return False
            count.set_text(str(left["n"]))
            left["n"] -= 1
            return True
        tick()
        state["timer"] = GLib.timeout_add_seconds(1, tick)
        buttons.pack_start(ui.button("Back", "btn pill btn-ghost", back), False, False, 0)
    else:
        buttons.pack_start(ui.button("Try again", "btn pill btn-primary",
                                     lambda: app.start_session(conn)), False, False, 0)
        buttons.pack_start(ui.button("Edit connection", "btn pill btn-ghost",
                                     lambda: (app.pop_layer(), app.edit_connection(conn))), False, False, 0)
    col.pack_start(buttons, False, False, 0)
    if kind == "signin":
        col.connect("destroy", lambda *_: stop())
        return col

    # Show details: the raw FreeRDP result, for IT
    if kind == "vpn":
        text = "WireGuard (NetworkManager)"
    elif conn.get("protocol") == "web":
        text = f"Chromium exit code {code}" if code else "No answer from the server"
    else:
        text = f"xfreerdp3 exit code {code}"
    if details.strip():
        text += "\n" + "\n".join(details.strip().splitlines()[-6:])
    rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
    det = ui.label(text, "details")
    det.set_selectable(True)
    det.set_line_wrap(True)
    det.set_max_width_chars(70)
    rev.add(det)
    toggle = ui.add_class(Gtk.Button(halign=Gtk.Align.CENTER, margin_top=14), "toggle-details")
    tl = Gtk.Label(label="Show details")
    toggle.add(tl)

    def flip(*_):
        rev.set_reveal_child(not rev.get_reveal_child())
        tl.set_text("Hide details" if rev.get_reveal_child() else "Show details")
    toggle.connect("clicked", flip)
    col.pack_start(toggle, False, False, 0)
    col.pack_start(rev, False, False, 0)
    col.connect("destroy", lambda *_: stop())
    return col
