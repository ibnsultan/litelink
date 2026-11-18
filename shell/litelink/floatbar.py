"""Connection bar for web sessions, modelled on the Remote Desktop connection bar:
a slim toolbar flush with the top edge of the screen.

    [pin] [network]        Connection name        [×]

- Closed, it's a small tab with a chevron sticking out of the top edge; clicking
  it slides the bar down.
- Pin keeps the bar on screen. Unpinned, it slides back into the tab a moment
  after the pointer leaves it.
- × ends the session (web pages have no sign-out of their own): back to the
  desktop, or in kiosk mode a fresh start.

It's an override-redirect popup kept above Chromium's full-screen window. Thin
clients run without a compositor, so the window can't have see-through corners;
instead it's cut to a rounded outline with the X11 shape extension.
"""
import math

import cairo
from gi.repository import Gdk, GLib, Gtk

from . import system, ui

WIDTH = 440
RADIUS = 7           # bottom corners (bar and tab)
ICON = "#3F434B"
HIDE_AFTER_MS = 1500


def rounded_bottom(w, h, r):
    """The w×h rectangle with its two bottom corners rounded (radius r), built
    from one-pixel rows (a shape mask has no anti-aliasing)."""
    region = cairo.Region(cairo.RectangleInt(0, 0, w, max(0, h - r)))
    for y in range(max(0, h - r), h):
        dy = y - (h - r) + 0.5
        inset = int(math.ceil(r - math.sqrt(max(0.0, r * r - dy * dy))))
        if w - 2 * inset > 0:
            region.union(cairo.RectangleInt(inset, y, w - 2 * inset, 1))
    return region


class FloatBar(Gtk.Window):
    def __init__(self, title, address, on_end):
        super().__init__(type=Gtk.WindowType.POPUP)
        ui.add_class(self, "floatbar-window")
        self.pinned = False
        self.hide_timer = None

        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        # The bar, slid down from the top edge
        self.rev = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=150)
        bar = ui.add_class(Gtk.Box(), "floatbar-bar")
        bar.set_size_request(WIDTH, -1)
        left = Gtk.Box(spacing=2)
        self.pin = self._icon_button("pin", "Pin the connection bar", self._toggle_pin)
        left.pack_start(self.pin, False, False, 0)
        net = ui.icon("signal", 14, ICON, 2.2)
        net.set_margin_start(6)
        self.net = Gtk.EventBox()
        self.net.add(net)
        self.net.set_tooltip_text("Network")
        left.pack_start(self.net, False, False, 0)
        bar.pack_start(left, False, False, 0)
        name = ui.label(title, "floatbar-title", xalign=0.5)
        name.set_ellipsize(3)
        name.set_tooltip_text(address)
        bar.set_center_widget(name)
        bar.pack_end(self._icon_button("close", "End session", on_end), False, False, 0)
        self.rev.add(bar)
        self.rev.connect("notify::child-revealed", self._settled)
        col.pack_start(self.rev, False, False, 0)
        # Closed: a small tab with a chevron
        self.tab = ui.add_class(Gtk.Button(), "floatbar-tab")
        self.tab.set_halign(Gtk.Align.CENTER)
        self.tab.set_can_focus(False)
        self.tab.set_tooltip_text("Connection bar")
        self.tab.add(ui.icon("chev_down", 12, "#5A606B", 2.4))
        self.tab.connect("clicked", lambda *_: self.open())
        col.pack_start(self.tab, False, False, 0)
        self.add(col)

        # Unpinned: slide away a moment after the pointer leaves
        self.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("enter-notify-event", lambda *_: self._cancel_hide())
        self.connect("leave-notify-event", self._left)
        self.connect("size-allocate", self._allocated)
        self.show_all()
        self.rev.hide()
        self.resize(1, 1)
        system.in_background(system.network_status, self._network)
        # Chromium maps its own window after this one: keep the bar above it
        self.raise_timer = GLib.timeout_add(1500, self._raise)

    @staticmethod
    def _icon_button(icon_name, tip, cb):
        b = ui.add_class(Gtk.Button(valign=Gtk.Align.CENTER), "floatbar-btn")
        b.set_can_focus(False)
        b.set_tooltip_text(tip)
        b.add(ui.icon(icon_name, 14, ICON, 2))
        b.connect("clicked", lambda *_: cb())
        return b

    def _network(self, st):
        name = st.get("name") or "Network"
        self.net.set_tooltip_text(name)

    # ---------------------------------------------------------------- window
    def _allocated(self, *_):
        w, h = self.get_size()
        self.move((self.get_screen().get_width() - w) // 2, 0)
        region = rounded_bottom(w, h, min(RADIUS, h // 2))
        self.shape_combine_region(region)          # what's drawn
        self.input_shape_combine_region(region)    # where clicks land

    def _raise(self):
        win = self.get_window()
        if win is not None:
            win.raise_()
        return True

    # ---------------------------------------------------------------- open / close
    def open(self):
        self.tab.hide()
        self.rev.show()
        self.rev.set_reveal_child(True)

    def collapse(self):
        self._cancel_hide()
        self.rev.set_reveal_child(False)

    def _settled(self, *_):
        # The window grows by itself as the bar slides down; once it has slid
        # back up, show the tab again and shrink the window around it
        if not self.rev.get_child_revealed():
            self.rev.hide()            # a slid-up revealer still keeps the bar's width
            self.tab.show()
            self.resize(1, 1)

    def _toggle_pin(self):
        self.pinned = not self.pinned
        ctx = self.pin.get_style_context()
        (ctx.add_class if self.pinned else ctx.remove_class)("active")
        self.pin.set_tooltip_text("Unpin the connection bar" if self.pinned else "Pin the connection bar")

    def _left(self, _w, ev):
        if ev.detail == Gdk.NotifyType.INFERIOR or self.pinned or not self.rev.get_reveal_child():
            return False
        self._cancel_hide()
        self.hide_timer = GLib.timeout_add(HIDE_AFTER_MS, lambda: (setattr(self, "hide_timer", None),
                                                                   self.collapse(), False)[2])
        return False

    def _cancel_hide(self):
        if self.hide_timer:
            GLib.source_remove(self.hide_timer)
            self.hide_timer = None
        return False

    def close(self):
        self._cancel_hide()
        if self.raise_timer:
            GLib.source_remove(self.raise_timer)
            self.raise_timer = None
        self.destroy()
