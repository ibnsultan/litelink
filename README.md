<img src="branding/logo/logo.png" alt="LiteLink OS" width="260">

LiteLink OS is a lightweight, Debian-based operating system for thin clients and small PCs.
Its job is to connect users to remote Windows and Linux desktops. It runs live from a USB
stick or installs to a small internal disk. Device setup, including networking, displays
and installation, is handled within the OS, so it can be deployed and used without
command-line knowledge.

Litelink version 2.0 (code named **Siamese**) is in active development. It is a rewrite on
Debian 13 and replaces the original Ubuntu 18.04 image. alot of things are unfinished; please
[report issues](https://github.com/ibnsultan/LiteLink-OS/issues) you run into.

## What it does

- **Remote desktop connections.** Each saved connection appears as a tile on the desktop.
  RDP is handled by FreeRDP 3, and LiteLink shows its own screens while connecting and when
  a sign-in fails, a certificate isn't trusted or a session drops.
- **Small footprint.** The ISO is about 760 MB. An installed system fits on a 2 GB Disk
- **Quiet start-up.** The boot menu, splash, desktop and power screens use LiteLink's own
  artwork instead of console text.
- **Settings.** Wi-Fi and Ethernet (including static IP and DNS), Bluetooth, display
  resolution (reverting automatically if a mode doesn't work), time zone and time servers.
- **Runs from a USB stick.** Settings can be saved on the same stick, so connections and
  Wi-Fi networks are kept across restarts.
- **VPN (WireGuard).** Reach desktops on a private network. Import the `.conf` file from a
  USB drive, or enter the details by hand, in **Settings › VPN**.

## Hardware

LiteLink targets x86-64 thin clients (Dell Wyse, HP t-series, IGEL, Fujitsu and similar)

|           |                                                                            |
| --------- | -------------------------------------------------------------------------- |
| Processor | 64-bit x86 (amd64)                                                         |
| Memory    | 2 GB (tested)                                                              |
| Storage   | 4 GB internal disk to install (eMMC, SSD or HDD) or it can be run from USB |
| Firmware  | UEFI or legacy BIOS to run from USB. **Installing needs UEFI**       |

So far it has been tested on a **Dell Wyse 3040** and in VirtualBox. Reports from other
devices are welcome.

## Getting started

### 1. Write the ISO to a USB stick

Download the ISO from [Releases](https://github.com/ibnsultan/LiteLink-OS/releases), or [build it yourself](#building-from-source). Write it to a USB stick (2 GB or larger) with
 a disk imaging utilitie like Rufus or Balena Etcher in **DD image mode**.

Write the image as-is (DD mode). LiteLink keeps your settings in the free space on the stick,
which only works when the stick holds the image itself rather than a copy of its files.

### 2. Boot it

Choose the USB stick in your device's boot menu. LiteLink's boot menu offers:

| Entry                             | What it does                                              |
| --------------------------------- | --------------------------------------------------------- |
| **Start LiteLink OS**       | The normal start (default)                                |
| Start LiteLink OS (run from RAM)  | Loads everything into memory, so the stick can be removed |
| Start LiteLink OS (safe graphics) | For displays or GPUs that show a black screen             |
| **Install LiteLink OS**     | Starts straight into the installer                        |

### 3. Keep your settings (optional)

When running from a USB stick, LiteLink offers to **set up settings storage** the first time
it starts. This adds a small partition in the stick's free space and leaves the rest of the
stick unchanged. From the next start, your connections, Wi-Fi networks, Bluetooth devices and
time settings are kept. You can also do this later in **Settings > Install**.

### 4. Install to the internal disk

Click **Install LiteLink** on the desktop, or open **Settings > Install**. The wizard has
these steps:

1. **Welcome**: the version and build being installed
2. **Time & region**: time zone, and automatic or manual date and time
3. **Features**: the mode (Standard; Kiosk is planned) and the connection types to install
   (RDP is always included)
4. **Storage**: the disk to install on
5. **Confirm**: the erase warning, and whether to include maintenance tools. The button
   becomes available after 10 seconds
6. **Install** and **Finish**: remove the stick and restart

The chosen disk is **erased** and split into three partitions:

| Partition         | Size             | Holds                                  |
| ----------------- | ---------------- | -------------------------------------- |
| EFI               | 256 MB           | The boot menu                          |
| `LITELINK`      | 3 GB             | The operating system image (read-only) |
| `litelink-data` | rest of the disk | Your settings                          |

Because the system image is read-only, each restart returns the device to the same state,
with only your settings carried over.

## Using LiteLink

| Dock (left)          |                                                          |
| -------------------- | -------------------------------------------------------- |
| **Kite** (top) | Settings menu. **Add connection** is the last item |
| Network              | Connection status and IP address, Wi-Fi on/off           |
| Volume               | Output volume                                            |
| Display              | Resolution                                               |
| Power                | Lock, restart or shut down. Esc cancels                  |

### VPN

Open **Settings › VPN** and click **Add tunnel**. Plug in a USB drive (FAT32, exFAT or NTFS)
holding the WireGuard `.conf` file from your IT team. LiteLink lists the `.conf` files it
finds, shows what the chosen one contains, and imports it. The drive is mounted read-only
and released when the dialog closes. **Enter the details manually** opens a form instead,
pre-filled from the chosen file if there is one. Each tunnel has an on/off switch and can
connect automatically at startup.

A connection that needs a private network can name its tunnel (the **VPN** field in the
connection's settings). Opening that connection brings the tunnel up first.

### Maintenance tools

When running from a USB stick, **Ctrl+Alt+T** opens a terminal, and `lspci`, `lsusb` and
other hardware tools are available for troubleshooting. An installed device keeps them only if
**Include maintenance tools** was ticked on the installer's Confirm page. Without them there
is no terminal and no general root access; the shell keeps only the few root actions its
settings need.

## Building from source

The ISO is built with Debian's [live-build](https://live-team.pages.debian.net/live-manual/)
on a **Debian 13 (trixie)** machine or VM, as root.

```bash
sudo make deps
```

```bash
sudo make iso
```

The image is written to `out/litelink-YYYYMMDD-amd64.iso`, and the build prints its size.

| Target                              | Does                                                                            |
| ----------------------------------- | ------------------------------------------------------------------------------- |
| `make deps`                       | Installs the build tools (live-build, ImageMagick, GRUB, Inter font)            |
| `make branding`                   | Renders the boot menu, splash animation and shell artwork from `branding/`    |
| `make shell`                      | Copies the desktop shell from `shell/` into the image                         |
| `make iso`                        | All of the above, then a clean live-build run                                   |
| `make sizes`                      | Prints the ISO, root filesystem and squashfs sizes                              |
| `make clean` / `make distclean` | Removes build output (`distclean` also clears the package cache and `out/`) |

A full build takes about 20–30 minutes and needs around 10 GB of free disk space.

### Repository layout

```
branding/          Logo, wallpaper and boot art; rendered by branding/render.sh
  grub/ isolinux/  Boot menus (UEFI and legacy BIOS)
  plymouth/        Boot splash theme
  motion/          Turns the logo animation into frames (splash, connecting, installer)
build/
  auto/config      live-build options (Debian 13, minimal, kernel command line)
  config/
    package-lists/ Packages in the image (each one commented)
    hooks/         Build-time setup: user, auto-login, sudo rules, OS identity
    includes.chroot/  Files copied into the image, including the root helpers in
                   usr/local/sbin (litelink-storage, litelink-state, litelink-restore,
                   litelink-media, litelink-vpn)
shell/             The desktop shell (Python 3 + GTK 3)
  main.py
  theme.css
  litelink/        app, dock flyouts, settings, installer, connection screens
docs/              Notes
Makefile
```

### How it fits together

- **Boot:** GRUB (UEFI) or isolinux (BIOS) loads the kernel with a silent console, and
  Plymouth plays the kite animation. The user is logged in automatically on tty1, X starts
  with Openbox, and the LiteLink shell opens. The splash hands over to the desktop without
  a black frame.
- **Shell:** a single full-screen GTK window for the desktop, dock, flyouts, settings and
  connection screens. Connections are stored in `~/.config/litelink`.
- **Root actions** (network, VPN tunnels, reading USB drives, time, Bluetooth radio,
  storage and install) go through a few
  small helpers that the shell may run with `sudo`. Nothing else gets root.
- **Saved settings:** live-boot's persistence restores only chosen folders from the
  settings partition: the shell's settings, NetworkManager connections, Bluetooth
  pairings and `/var/lib/litelink` (time zone, time servers, install choices). Everything
  else starts fresh on every boot.

## Roadmap

These appear greyed out in the interface until they're ready:

- **Kiosk mode:** a single connection shown full screen, for shared devices
- **Connection types:** VNC, remote workspaces (Citrix Workspace, VMware Horizon) and web kiosk
- **Settings:** Remote connections, Keyboard & input, Sound, Admin lock, Diagnostics
- Changing the mode and connection types after installation, from Settings

## Credits

Built on [Debian](https://www.debian.org/) 13 with
[live-build](https://salsa.debian.org/live-team/live-build),
[FreeRDP](https://www.freerdp.com/), [GTK](https://www.gtk.org/),
[Openbox](http://openbox.org/), [Plymouth](https://gitlab.freedesktop.org/plymouth/plymouth),
NetworkManager, PipeWire, BlueZ and the [Inter](https://rsms.me/inter/) typeface.

Issues and ideas: [github.com/ibnsultan/LiteLink-OS/issues](https://github.com/ibnsultan/LiteLink-OS/issues)
