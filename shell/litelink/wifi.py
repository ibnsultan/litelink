"""Joining a Wi-Fi network in place.

A network's row opens below itself (no dialog) in two steps: first Connect
automatically and Connect; then, for a secured network that isn't saved, the
password (with a Show button) and Cancel / Connect. While it connects, the row
shows what NetworkManager is doing ("Checking the password…", "Getting an IP
address…"); a failure says why and leaves the row open to try again. One row
is open at a time. Used by the dock's Network menu and Settings › Network."""
from gi.repository import GLib, Gtk

from . import system, ui

# NetworkManager device states while connecting -> what the row shows
PHASES = (("prepare", "Preparing…"), ("configuring", "Joining the network…"),
          ("need auth", "Checking the password…"), ("ip configuration", "Getting an IP address…"),
          ("ip connectivity", "Checking the connection…"), ("secondar", "Checking the connection…"))

ERRORS = {"password": "Wrong password. Check it and try again.",
          "short": "The password is at least 8 characters.",
          "notfound": "The network is out of range. Move closer and try again.",
          "timeout": "The network didn’t answer in time. Try again.",
          "failed": "Couldn’t connect to this network."}


def is_secured(n):
    return n["security"] not in ("", "Open", "--")


class Group:
    """The rows of one list: opening one closes the one open before."""

    def __init__(self):
        self.open_row = None


def signal_icon(signal):
    return "wifi3" if signal > 66 else "wifi2" if signal > 33 else "wifi1"


class NetworkRow(Gtk.Box):
    """One nearby network. on_joined() runs after it connected (to refresh the
    list). on_click, when given, replaces opening the row (e.g. the kiosk asks
    for the admin password first)."""

    def __init__(self, n, group, on_joined, saved=False, on_click=None, icon_color="#3F434B",
                 form_width=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        ui.add_class(self, "wifi-item")
        self.n, self.group, self.on_joined = n, group, on_joined
        self.secured = is_secured(n)
        # A saved network connects with its stored password; asked only if that fails
        self.ask_pw = self.secured and not saved
        self.busy = False
        self.step = 1

        head = ui.add_class(Gtk.Button(), "flyout-row")
        row = Gtk.Box(spacing=10)
        row.pack_start(ui.icon(signal_icon(n["signal"]), 16, icon_color), False, False, 0)
        name = ui.label(n["ssid"], "body")
        name.set_ellipsize(3)
        row.pack_start(name, True, True, 0)
        self.state = ui.label("Secured" if self.secured else "Open", "faint small")
        row.pack_start(self.state, False, False, 0)
        head.add(row)
        head.connect("clicked", lambda *_: on_click() if on_click else self.toggle())
        self.pack_start(head, False, False, 0)

        self.rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN,
                                transition_duration=150)
        form = self._form()
        if form_width:                     # wide lists (Settings): keep it flyout-sized
            form.set_size_request(form_width, -1)
            form.set_halign(Gtk.Align.START)
        self.rev.add(form)
        self.pack_start(self.rev, False, False, 0)

    # -- the part that opens ------------------------------------------------------
    def _form(self):
        form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=6,
                       margin_bottom=8, margin_start=26)
        self.pw_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.pw_box.pack_start(ui.label("Enter the password", "muted small"), False, False, 0)
        self.pw = Gtk.Entry(visibility=False)
        self.pw.set_icon_from_pixbuf(Gtk.EntryIconPosition.SECONDARY, ui.icon_pixbuf("eye", 18, "#5A606B"))
        self.pw.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "Show password")
        self.pw.connect("icon-press", lambda *_: self._show_pw(not self.pw.get_visibility()))
        self.pw.connect("activate", lambda *_: self.connect_now())
        self.pw.connect("changed", lambda *_: self._status("", None))
        self.pw_box.pack_start(self.pw, False, False, 0)
        self.pw_box.set_no_show_all(True)
        form.pack_start(self.pw_box, False, False, 0)

        self.auto = Gtk.CheckButton(label="Connect automatically", active=True)
        form.pack_start(self.auto, False, False, 0)

        # Status: a spinner while connecting, the reason when it failed
        self.st = Gtk.Box(spacing=8)
        self.spin = Gtk.Spinner()
        self.st.pack_start(self.spin, False, False, 0)
        self.msg = ui.label("", "small")
        self.msg.set_line_wrap(True)
        self.msg.set_max_width_chars(34)
        self.st.pack_start(self.msg, True, True, 0)
        self.st.set_no_show_all(True)        # only while there's something to say
        form.pack_start(self.st, False, False, 0)

        # Step 1: Connect at the right. Step 2 (password): Cancel | Connect
        btns = Gtk.Box(spacing=8, homogeneous=True)
        self.blank = Gtk.Label()
        self.cancel = ui.button("Cancel", "btn btn-sm", self.close)
        self.go = ui.button("Connect", "btn btn-sm btn-primary", self.next)
        for w in (self.blank, self.cancel, self.go):
            btns.pack_start(w, True, True, 0)
        form.pack_start(btns, False, False, 0)
        for w in (self.auto, self.blank, self.cancel):
            w.set_no_show_all(True)
        return form

    @staticmethod
    def _shown(w, on):
        # no_show_all keeps it out of the list's show_all; it also blocks show_all here
        w.set_no_show_all(not on)
        if on:
            w.show_all()
        else:
            w.hide()

    def _step(self, n):
        """1: Connect automatically + Connect. 2: the password, Cancel + Connect."""
        self.step = n
        self._shown(self.auto, n == 1)
        self._shown(self.blank, n == 1)
        self._shown(self.pw_box, n == 2)
        self._shown(self.cancel, n == 2)
        if n == 2:
            GLib.idle_add(self.pw.grab_focus)

    def next(self):
        if self.step == 1 and self.ask_pw:
            self._step(2)
        else:
            self.connect_now()

    def _show_pw(self, on):
        self.pw.set_visibility(on)
        self.pw.set_icon_from_pixbuf(Gtk.EntryIconPosition.SECONDARY,
                                     ui.icon_pixbuf("eye_off" if on else "eye", 18, "#5A606B"))
        self.pw.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY,
                                      "Hide password" if on else "Show password")

    def _status(self, text, kind):
        """kind: None (plain), 'busy' (spinner), 'ok', 'err'."""
        self.msg.set_text(text)
        ctx = self.msg.get_style_context()
        for c in ("ok", "err", "muted"):
            ctx.remove_class(c)
        ctx.add_class({"ok": "ok", "err": "err"}.get(kind, "muted"))
        self.st.set_visible(bool(text))
        self.msg.show()
        self.spin.set_visible(kind == "busy")
        if kind == "busy":
            self.spin.start()
        else:
            self.spin.stop()

    # -- open / close -------------------------------------------------------------
    def toggle(self):
        if self.rev.get_reveal_child():
            self.close()
        else:
            self.open()

    def open(self):
        if self.group.open_row not in (None, self):
            self.group.open_row.close()
        self.group.open_row = self
        self._step(1)
        ui.add_class(self, "open")
        self.rev.set_reveal_child(True)

    def close(self):
        if self.busy:
            return
        self.rev.set_reveal_child(False)
        self.get_style_context().remove_class("open")
        self._status("", None)
        if self.group.open_row is self:
            self.group.open_row = None

    # -- connecting ---------------------------------------------------------------
    def _set_busy(self, on):
        self.busy = on
        for w in (self.pw, self.auto, self.go, self.cancel):
            w.set_sensitive(not on)

    def connect_now(self):
        if self.busy:
            return
        password = self.pw.get_text() if self.ask_pw else ""
        if self.ask_pw:
            if not password:
                self.pw.grab_focus()
                return
            if len(password) < 8 and "WEP" not in self.n["security"]:
                self._status(ERRORS["short"], "err")
                self.pw.grab_focus()
                return
        self._set_busy(True)
        self.state.set_text("Connecting…")
        self._status("Connecting…", "busy")
        GLib.timeout_add(700, self._poll)
        auto = self.auto.get_active()
        system.in_background(lambda: system.wifi_connect(self.n["ssid"], password, auto), self._done)

    def _poll(self):
        """Show NetworkManager's progress while the connect runs."""
        if not self.busy or self.get_ancestor(Gtk.Window) is None:
            return False

        def show(state):
            if not self.busy:
                return
            state = state.lower()          # e.g. "connecting (getting IP configuration)"
            phase = next((text for key, text in PHASES if key in state), None)
            if phase:
                self._status(phase, "busy")
        system.in_background(system.wifi_state, show)
        return True

    def _done(self, res):
        ok, why = res
        if self.get_ancestor(Gtk.Window) is None:     # the list closed meanwhile
            self.busy = False
            return
        if ok:                                        # stays disabled until the list refreshes
            self.state.set_text("Connected")
            self.state.get_style_context().remove_class("faint")
            self.state.get_style_context().add_class("ok")
            self._status("Connected", "ok")
            GLib.timeout_add(700, lambda: (self.on_joined(), False)[1])
            return
        self._set_busy(False)
        self.state.set_text("Secured" if self.secured else "Open")
        if why == "password" and not self.ask_pw:
            # The saved password no longer works (or the network needs one): ask for it
            self.ask_pw = True
            self._step(2)
            self._status("The saved password didn’t work. Enter the password.", "err")
        else:
            self._status(ERRORS.get(why, ERRORS["failed"]), "err")
        if self.ask_pw:
            self.pw.grab_focus()
            self.pw.select_region(0, -1)
