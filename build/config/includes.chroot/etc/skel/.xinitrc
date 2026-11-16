#!/bin/sh
# LiteLink OS X session

# First paint: the splash's resting frame, centred on the splash colour, so
# the screen goes straight from boot splash to desktop with no black frame.
hsetroot -solid "#101217" -center /usr/share/litelink/handover.png 2>/dev/null \
    || xsetroot -solid "#101217"
# Quit the splash and make sure X really owns the display (see the helper).
sudo -n /usr/local/sbin/litelink-drm-handover 2>/dev/null

xset s off -dpms
exec openbox-session
