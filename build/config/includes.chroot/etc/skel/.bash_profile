# LiteLink OS: start the graphical session on tty1
[ -f ~/.bashrc ] && . ~/.bashrc
if [ -z "$DISPLAY" ] && [ "$(tty)" = /dev/tty1 ]; then
    # Flicker-free handover from the boot splash (same sequence GDM uses):
    # Plymouth lets go of the display but leaves its last frame up; X starts
    # on top, ~/.xinitrc paints the same frame, then quits Plymouth.
    sudo -n /usr/bin/plymouth deactivate 2>/dev/null
    exec startx -- -nolisten tcp >/tmp/litelink-x.log 2>&1
fi
