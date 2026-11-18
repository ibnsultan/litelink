"""Shared UI helpers: icons, the kite, buttons, colours."""
import glob
import os

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk  # noqa: E402

SHARE = os.environ.get("LITELINK_SHARE", "/usr/share/litelink")

BLUE = "#2F6FD6"
INK = "#16171A"
WHITE = "#FFFFFF"

ICONS = {
    "network": "M2 8.8a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01",
    "ethernet": "M4 4h16v10H4zM9 18h6M12 14v4",
    "volume": "M11 5 6 9H3v6h3l5 4V5zM15.5 8.5a5 5 0 0 1 0 7M18.5 5.5a9 9 0 0 1 0 13",
    "display": "M3 4h18v12H3zM8 20h8M12 16v4",
    "monitor": "M3 4h18v12H3zM8 20h8M12 16v4",
    "system": "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16v-4M12 8h.01",
    "settings": "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z",
    "power": "M18.4 6.6a9 9 0 1 1-12.8 0M12 2v10",
    "restart": "M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5",
    "lock": "M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4",
    "logoff": "M9 21H5V3h4M16 17l5-5-5-5M21 12H9",
    "plus": "M12 5v14M5 12h14",
    "close": "M6 6l12 12M18 6 6 18",
    "arrow": "M5 12h14M13 6l6 6-6 6",
    "keyboard": "M3 6h18v12H3zM7 10h.01M11 10h.01M15 10h.01M7 14h10",
    "sound": "M11 5 6 9H3v6h3l5 4V5zM15.5 8.5a5 5 0 0 1 0 7",
    "time": "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2",
    "remote": "M3 4h18v12H3zM8 20h8M12 16v4",
    "diag": "M22 12h-4l-3 9L9 3l-3 9H2",
    "warn": "M12 8v5M12 16.5h.01",
    "shield": "M12 3l7 3v5c0 4.5-3 8-7 10-4-2-7-5.5-7-10V6zM12 9v4M12 16h.01",
    "reconnect": "M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3M18 3v4h-4M6 21v-4h4",
    "chev_down": "M6 9l6 6 6-6",
    "chev_right": "M9 6l6 6-6 6",
    "check": "M5 12.5l4.5 4.5L19 7.5",
    "bluetooth": "M7 7l10 10-5 5V2l5 5L7 17",
    "keyboard_dev": "M3 6h18v12H3zM7 10h.01M11 10h.01M15 10h.01M7 14h10",
    "mouse": "M12 3a6 6 0 0 0-6 6v6a6 6 0 0 0 12 0V9a6 6 0 0 0-6-6zM12 7v4",
    "headset": "M4 15v-3a8 8 0 0 1 16 0v3M4 15a2 2 0 0 0 2 2h1v-5H6a2 2 0 0 0-2 2zM20 15a2 2 0 0 1-2 2h-1v-5h1a2 2 0 0 1 2 2z",
    "device": "M7 3h10v18H7zM11 18h2",
    "storage": "M3 13h18v6H3zM3 13l3-8h12l3 8M7 16h.01",
    "maximize": "M5 5h14v14H5z",
    "restore": "M8 8h11v11H8zM5 16V5h11",
    "chev_up": "M6 15l6-6 6 6",
    "vpn": "M13 15.5a5.5 5.5 0 1 1-11 0 5.5 5.5 0 0 1 11 0zM11.4 11.6 21 2M15.5 7.5l3 3L22 7l-3-3",
    "eye": "M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
    "eye_off": "M3 3l18 18M10.6 5.1A10.4 10.4 0 0 1 12 5c6.5 0 10 7 10 7a17.6 17.6 0 0 1-3.2 4.2M6.6 6.6C3.9 8.4 2 12 2 12s3.5 7 10 7c1.9 0 3.6-.6 5-1.4M9.9 9.9a3 3 0 0 0 4.2 4.2",
    "volume_mute": "M11 5 6 9H3v6h3l5 4V5zM22 9l-6 6M16 9l6 6",
    "usb": "M8 3h8v6H8zM6 9h12v9a3 3 0 0 1-3 3H9a3 3 0 0 1-3-3zM10 5.5h.01M14 5.5h.01",
    "file": "M14 3H6v18h12V7zM14 3v4h4",
    "back": "M15 6l-6 6 6 6",
    "pin": "M12 16v5M8 4h8M9.5 4l-.7 6.2L6 13v1h12v-1l-2.8-2.8L14.5 4",
    "signal": "M5 19v-2M10 19v-6M15 19v-10M20 19V5",
    "wifi3": "M2 8.8a15 15 0 0 1 20 0M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01",
    "wifi2": "M5 12.5a10 10 0 0 1 14 0M8.5 16a5 5 0 0 1 7 0M12 19.5h.01",
    "wifi1": "M8.5 16a5 5 0 0 1 7 0M12 19.5h.01",
}

_cache = {}


def _svg_pixbuf(svg, w, h):
    loader = GdkPixbuf.PixbufLoader.new_with_type("svg")
    loader.set_size(w, h)
    loader.write(svg.encode())
    loader.close()
    return loader.get_pixbuf()


def icon_pixbuf(name, size=22, color=INK, stroke=1.8):
    key = ("i", name, size, color, stroke)
    if key not in _cache:
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 24 24" '
               f'fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
               f'stroke-linejoin="round"><path d="{ICONS[name]}"/></svg>')
        _cache[key] = _svg_pixbuf(svg, size, size)
    return _cache[key]


def icon(name, size=22, color=INK, stroke=1.8):
    return Gtk.Image.new_from_pixbuf(icon_pixbuf(name, size, color, stroke))


_KITE_D = None


def kite(size, color="#F2F5FA", opacity=1.0):
    """The LiteLink kite mark as a Gtk.Image."""
    global _KITE_D
    key = ("k", size, color, opacity)
    if key not in _cache:
        if _KITE_D is None:
            import re
            with open(os.path.join(SHARE, "kite.svg")) as f:
                _KITE_D = re.search(r'\sd="([^"]+)"', f.read()).group(1)
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-20 -20 552 552" width="{size}" height="{size}">'
               f'<path d="{_KITE_D}" fill="{color}" fill-opacity="{opacity}" fill-rule="evenodd"/></svg>')
        _cache[key] = _svg_pixbuf(svg, size, size)
    return Gtk.Image.new_from_pixbuf(_cache[key])


def picture(name, width):
    """A PNG from /usr/share/litelink scaled to width."""
    path = os.path.join(SHARE, name)
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, width, -1, True)
        return Gtk.Image.new_from_pixbuf(pb)
    except GLib.Error:
        return Gtk.Label(label="LiteLink")


def label(text, cls=None, xalign=0.0, wrap=False):
    lb = Gtk.Label(label=text, xalign=xalign)
    if wrap:
        # Paragraphs get airier lines (GTK 3 has no CSS line-height; Pango does)
        lb.set_markup(f'<span line_height="1.4">{GLib.markup_escape_text(text)}</span>')
        lb.set_line_wrap(True)
        lb.set_max_width_chars(60)
        lb.set_justify(Gtk.Justification.CENTER if xalign == 0.5 else Gtk.Justification.LEFT)
    for c in (cls or "").split():
        lb.get_style_context().add_class(c)
    return lb


def button(text, cls="btn", on_click=None, icon_name=None, icon_color=WHITE):
    b = Gtk.Button()
    box = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
    if icon_name:
        box.pack_start(icon(icon_name, 16, icon_color), False, False, 0)
    if text:
        box.pack_start(Gtk.Label(label=text), False, False, 0)
    b.add(box)
    for c in cls.split():
        b.get_style_context().add_class(c)
    b.set_relief(Gtk.ReliefStyle.NONE)
    if on_click:
        b.connect("clicked", lambda *_: on_click())
    return b


def count_button(text, cls, on_click, icon_name=None, fg=WHITE):
    """A button that carries its own countdown: [icon] Label | 30.
    Returns (button, count_label)."""
    b = Gtk.Button()
    box = Gtk.Box(spacing=10, halign=Gtk.Align.CENTER)
    if icon_name:
        box.pack_start(icon(icon_name, 16, fg, 2), False, False, 0)
    box.pack_start(Gtk.Label(label=text), False, False, 0)
    sep = Gtk.Box()
    sep.get_style_context().add_class("count-sep")
    sep.set_valign(Gtk.Align.CENTER)
    box.pack_start(sep, False, False, 2)
    count = Gtk.Label(label="")
    count.get_style_context().add_class("count")
    count.set_width_chars(2)
    box.pack_start(count, False, False, 0)
    b.add(box)
    for c in cls.split():
        b.get_style_context().add_class(c)
    b.set_relief(Gtk.ReliefStyle.NONE)
    b.connect("clicked", lambda *_: on_click())
    return b, count


def add_class(widget, *classes):
    ctx = widget.get_style_context()
    for c in classes:
        ctx.add_class(c)
    return widget


from gi.repository import Gdk, GObject  # noqa: E402


def make_draggable(panel, handle, on_double=None):
    """Let the user move a centred panel (a shell layer) by dragging `handle`.
    The panel stays inside its layer; double-click calls on_double (maximize)."""
    handle.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                      | Gdk.EventMask.POINTER_MOTION_MASK)
    st = {"drag": False}

    def press(_w, e):
        if e.button != 1 or getattr(panel, "maximized", False) and e.type != Gdk.EventType._2BUTTON_PRESS:
            return False
        if e.type == Gdk.EventType._2BUTTON_PRESS:
            if on_double:
                on_double()
            return True
        a = panel.get_allocation()
        st.update(drag=True, x=a.x, y=a.y, px=e.x_root, py=e.y_root)
        panel.set_halign(Gtk.Align.START)
        panel.set_valign(Gtk.Align.START)
        panel.set_margin_start(a.x)
        panel.set_margin_top(a.y)
        panel.set_margin_end(0)
        panel.set_margin_bottom(0)
        return True

    def motion(_w, e):
        if not st["drag"]:
            return False
        area = panel.get_parent().get_allocation()
        a = panel.get_allocation()
        nx = int(st["x"] + e.x_root - st["px"])
        ny = int(st["y"] + e.y_root - st["py"])
        panel.set_margin_start(max(0, min(nx, area.width - a.width)))
        panel.set_margin_top(max(0, min(ny, area.height - a.height)))
        return True

    def release(*_):
        st["drag"] = False
        return False

    # No hand cursor on the drag area: title bars keep the arrow, like native ones
    handle.connect("button-press-event", press)
    handle.connect("motion-notify-event", motion)
    handle.connect("button-release-event", release)


# ---------------------------------------------------------------- pointer cursor
# GTK 3 has no CSS `cursor`, so clickable widgets would show the arrow. One hook
# for the whole shell gives every button, switch, list and `clickable`-classed
# widget the pointing-hand cursor while it's enabled.
CLICKABLE = (Gtk.Button, Gtk.Switch, Gtk.ListBox)
_pointer = {}


def _is_clickable(w):
    return isinstance(w, CLICKABLE) or w.get_style_context().has_class("clickable")


def _cursor_for(w):
    if w.is_sensitive():
        if "c" not in _pointer:
            _pointer["c"] = Gdk.Cursor.new_from_name(w.get_display(), "pointer")
        return _pointer["c"]
    return None


def _on_enter(w, ev, *_):
    if _is_clickable(w) and ev.window is not None:
        if getattr(w, "_ll_cursor_win", None) is None:
            # First hover: follow enabled / disabled from now on (a per-widget
            # handler; a global state hook would also fire while widgets are
            # being constructed, which crashes PyGObject, e.g. Gtk.RadioButton()).
            w.connect("state-flags-changed", _on_state)
        w._ll_cursor_win = ev.window        # the widget's own input window
        ev.window.set_cursor(_cursor_for(w))
    return True                             # keep the hook


def _on_state(w, *_):
    # Disabled while hovered (or enabled again): update the cursor right away
    win = getattr(w, "_ll_cursor_win", None)
    if win is not None and not win.is_destroyed():
        win.set_cursor(_cursor_for(w))


def install_pointer_cursors():
    GObject.add_emission_hook(Gtk.Widget, "enter-notify-event", _on_enter)


class Dropdown(Gtk.MenuButton):
    """LiteLink's select field. GTK 3's own combo box pops up a menu aligned to
    the current item (leaving a large empty area above long lists); this opens
    a compact, scrollable list right below the field instead.

    API mirrors Gtk.ComboBoxText where the shell uses it: get_active_id(),
    set_active_id(), and the "changed" signal."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, options, current=None):
        super().__init__()
        add_class(self, "dropdown")
        self.options = [(str(k), t) for k, t in options]
        self.active = None
        box = Gtk.Box(spacing=8)
        self.lbl = Gtk.Label(xalign=0)
        self.lbl.set_ellipsize(3)            # Pango.EllipsizeMode.END
        box.pack_start(self.lbl, True, True, 0)
        box.pack_start(icon("chev_down", 16, "#5A606B", 2), False, False, 0)
        self.add(box)

        self.pop = Gtk.Popover(position=Gtk.PositionType.BOTTOM)
        add_class(self.pop, "dropdown-pop")
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_max_content_height(280)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.listbox.connect("row-activated", self._picked)
        self.checks = {}
        for key, text in self.options:
            row = Gtk.ListBoxRow()
            row.key = key
            rb = Gtk.Box(spacing=10)
            rl = Gtk.Label(label=text, xalign=0)
            rb.pack_start(rl, True, True, 0)
            chk = icon("check", 16, "#2F6FD6", 2.2)
            chk.set_no_show_all(True)
            rb.pack_start(chk, False, False, 0)
            row.add(rb)
            self.checks[key] = (row, chk)
            self.listbox.add(row)
        self.scroll.add(self.listbox)
        self.pop.add(self.scroll)
        self.scroll.show_all()
        self.set_popover(self.pop)
        self.connect("toggled", self._opening)
        self.set_active_id(current if current is not None else (self.options[0][0] if self.options else None))

    def _opening(self, *_):
        if self.get_active():
            self.scroll.set_size_request(max(self.get_allocated_width() - 12, 160), -1)
            if self.active in self.checks:
                self.checks[self.active][0].grab_focus()

    def _picked(self, _lb, row):
        self.set_active_id(row.key)
        self.pop.popdown()
        self.emit("changed")

    def get_active_id(self):
        return self.active

    def set_active_id(self, key):
        key = None if key is None else str(key)
        if key not in self.checks and self.options:
            key = self.options[0][0]
        self.active = key
        for k, (row, chk) in self.checks.items():
            ctx = row.get_style_context()
            (ctx.add_class if k == key else ctx.remove_class)("selected-row")
            chk.set_visible(k == key)
        self.lbl.set_text(dict(self.options).get(key, ""))


class FrameAnimation(Gtk.Image):
    """Plays a numbered PNG sequence (e.g. the connecting kite) in a loop."""

    def __init__(self, pattern, fps=25):
        super().__init__()
        self.frames = []
        for p in sorted(glob.glob(os.path.join(SHARE, pattern))):
            try:
                self.frames.append(GdkPixbuf.Pixbuf.new_from_file(p))
            except GLib.Error:
                pass
        self.i = 0
        self.timer = None
        self.interval = int(1000 / fps)
        if self.frames:
            self.set_from_pixbuf(self.frames[0])
        self.connect("map", self._start)
        self.connect("unmap", self._stop)

    def _start(self, *_):
        if self.frames and not self.timer:
            # Idle priority: on a slow CPU the animation yields to real work
            # (installer progress, log lines) instead of crowding it out.
            self.timer = GLib.timeout_add(self.interval, self._tick,
                                          priority=GLib.PRIORITY_DEFAULT_IDLE)

    def _stop(self, *_):
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = None

    def _tick(self):
        self.i = (self.i + 1) % len(self.frames)
        self.set_from_pixbuf(self.frames[self.i])
        return True
