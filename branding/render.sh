#!/bin/sh
# LiteLink OS - render boot branding from the brand sources.
#
# Run from the repo root on the build host (called by `make iso`).
# Needs: imagemagick, grub-common (grub-mkfont), fonts-inter.
# Output goes into build/config/ and is git-ignored: the files in
# branding/ are the only source of truth.
set -e

BRAND=branding
LOGO=$BRAND/logo
CFG=build/config
PLY=$CFG/includes.chroot/usr/share/plymouth/themes/litelink
GRUB=$CFG/bootloaders/grub-pc
ISOL=$CFG/bootloaders/isolinux
SHARE=$CFG/includes.chroot/usr/share/litelink

# Brand palette
INK="#16171A"        # wordmark / dark surfaces
BG_TOP="#0E1015"     # boot background, top
BG_BOT="#171A21"     # boot background, bottom
BLUE="#2F6FD6"       # primary
MUTED="#8B93A1"      # secondary text
TRACK="#262A33"      # progress track

INTER=$(fc-match -f '%{file}' 'Inter:style=Regular')
INTER_MED=$(fc-match -f '%{file}' 'Inter:style=Medium')
case "$INTER" in *Inter*) ;; *) echo "render.sh: Inter font not found (apt install fonts-inter)" >&2; exit 1;; esac

command -v convert >/dev/null || { echo "render.sh: imagemagick missing" >&2; exit 1; }
command -v grub-mkfont >/dev/null || { echo "render.sh: grub-mkfont missing (apt install grub-common)" >&2; exit 1; }

mkdir -p "$PLY" "$GRUB" "$ISOL" "$SHARE"

# Light version of the logo for dark backgrounds: blue kite, white wordmark.
LOGO_LIGHT=$(mktemp --suffix=.png)
trap 'rm -f "$LOGO_LIGHT"' EXIT
convert "$LOGO/logo.png" -fuzz 35% -fill white -opaque "$INK" "$LOGO_LIGHT"

text_png() { # text_png <out> <text> <size> <color> [font]
    convert -background none -fill "$4" -font "${5:-$INTER}" -pointsize "$3" \
        label:"$2" "$1"
}

# ---------------------------------------------------------------- Plymouth
# Animated kite from the LiteLink motion designs, rendered to frames.
rm -f "$PLY"/*.png
python3 "$BRAND/motion/render_frames.py" "$LOGO/litelink-kite.svg" "$PLY" 25 1.4
. "$PLY/frames.txt"
cp "$BRAND/plymouth/litelink.plymouth" "$PLY/"
sed -e "s/@FPS@/$fps/" -e "s/@BOOT_INTRO@/$boot_intro/" \
    -e "s/@BOOT_LOOP@/$boot_loop/" -e "s/@SHIMMER@/$shimmer/" \
    "$BRAND/plymouth/litelink.script" > "$PLY/litelink.script"
rm -f "$PLY/frames.txt"
text_png "$PLY/msg-shutdown.png" "Shutting down" 15 "$MUTED"
text_png "$PLY/msg-reboot.png"   "Restarting"    15 "$MUTED"

# ---------------------------------------------------------------- GRUB (EFI)
# Background is a plain gradient so it can stretch to any resolution; the
# logo is a separate theme image so it keeps its aspect ratio.
mkdir -p "$GRUB/live-theme"
cp "$BRAND/grub/config.cfg" "$BRAND/grub/grub.cfg" "$BRAND/grub/theme.cfg" "$GRUB/"
cp "$BRAND/grub/theme.txt" "$GRUB/live-theme/theme.txt"
# GRUB and syslinux only read 8-bit PNGs; force depth/colour type.
convert -size 1280x720 gradient:"$BG_TOP"-"$BG_BOT" -depth 8 PNG24:"$GRUB/splash.png"
convert "$LOGO_LIGHT" -resize 260x -depth 8 PNG32:"$GRUB/logo.png"
convert -size 8x8 xc:"$BLUE" -depth 8 PNG24:"$GRUB/select_c.png"
convert -size 8x8 xc:none -depth 8 PNG32:"$GRUB/menu_c.png"
grub-mkfont -s 18 -o "$GRUB/inter-regular-18.pf2" "$INTER"
grub-mkfont -s 14 -o "$GRUB/inter-regular-14.pf2" "$INTER"
grub-mkfont -s 18 -o "$GRUB/inter-medium-18.pf2" "$INTER_MED"

# ---------------------------------------------------------------- isolinux (BIOS)
cp "$BRAND"/isolinux/* "$ISOL/"
cp /usr/share/live/build/bootloaders/isolinux/isolinux.bin \
   /usr/share/live/build/bootloaders/isolinux/ldlinux.c32 "$ISOL/" 2>/dev/null || true
convert -size 640x480 gradient:"$BG_TOP"-"$BG_BOT" \
    \( "$LOGO_LIGHT" -resize 220x \) -gravity north -geometry +0+110 -composite \
    -depth 8 PNG24:"$ISOL/splash.png"

# ---------------------------------------------------------------- shared art
# Used by the desktop shell (Phase 3) and window icons.
cp "$LOGO/logo.png" "$SHARE/logo.png"
cp "$LOGO_LIGHT" "$SHARE/logo-light.png"
cp "$LOGO/litelink-kite.svg" "$SHARE/kite.svg"
cp "$LOGO/litelink-primary-icon.png" "$SHARE/icon.png"
# Desktop wallpaper (JPEG keeps the image small; the source PNG is ~1.5 MB)
convert "$BRAND/wallpaper/primary.png" -strip -quality 88 "$SHARE/wallpaper.jpg"
# Shell "Connecting" animation (motion 03)
rm -rf "$SHARE/connect"
python3 "$BRAND/motion/render_frames.py" "$LOGO/litelink-kite.svg" "$SHARE/connect" 25 1.0 connect
# Same motion in brand blue for light surfaces (installer wizard)
rm -rf "$SHARE/install"
python3 "$BRAND/motion/render_frames.py" "$LOGO/litelink-kite.svg" "$SHARE/install" 25 1.0 install
# Resting frame of the boot loop (dimmed kite); X paints it first so the
# splash-to-desktop handover has no black frame.
cp "$PLY/boot-loop-000.png" "$SHARE/handover.png"

echo "render.sh: branding rendered"
