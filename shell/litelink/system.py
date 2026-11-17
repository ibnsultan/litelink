"""Thin wrappers around system tools: NetworkManager, PipeWire, xrandr, power."""
import glob
import os
import platform
import re
import shutil
import socket
import subprocess


def run(cmd, timeout=5, sudo=False):
    if sudo:
        cmd = ["sudo", "-n"] + cmd
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return out.returncode, out.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


# ---------------------------------------------------------------- network
def network_status():
    """-> dict(kind, name, state, ip) for the primary connection."""
    rc, out = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
    best = {"kind": "none", "name": "Not connected", "state": "disconnected", "ip": ""}
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) < 4:
            continue
        dev, typ, state, con = parts[:4]
        if typ in ("ethernet", "wifi") and state == "connected":
            best = {"kind": typ, "name": "Ethernet" if typ == "ethernet" else con,
                    "state": "connected", "ip": ip_of(dev), "device": dev}
            if typ == "ethernet":
                break
    return best


def ip_of(dev):
    rc, out = run(["ip", "-4", "-o", "addr", "show", "dev", dev])
    m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else ""


def primary_ip():
    st = network_status()
    return st.get("ip", "")


def gateway_dns():
    rc, out = run(["ip", "route", "show", "default"])
    m = re.search(r"default via (\S+)", out)
    gw = m.group(1) if m else ""
    dns = []
    try:
        with open("/etc/resolv.conf") as f:
            dns = [l.split()[1] for l in f if l.startswith("nameserver")]
    except OSError:
        pass
    return gw, ", ".join(dns)


def _sys(dev, name):
    try:
        with open(f"/sys/class/net/{dev}/{name}") as f:
            return f.read().strip()
    except OSError:
        return ""


def adapters():
    """Every Ethernet and Wi-Fi adapter NetworkManager knows, with details for
    the settings cards."""
    rc, out = run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
    result = []
    for line in out.splitlines():
        parts = line.split(":")
        if len(parts) < 4 or parts[1] not in ("ethernet", "wifi"):
            continue
        dev, typ, state, con = parts[0], parts[1], parts[2], ":".join(parts[3:])
        rc, show = run(["nmcli", "-t", "-f", "GENERAL.VENDOR,GENERAL.PRODUCT,GENERAL.HWADDR,"
                        "IP4.ADDRESS,IP4.GATEWAY,IP4.DNS", "device", "show", dev])
        info = {"addr": [], "dns": []}
        for l in show.splitlines():
            k, _, v = l.partition(":")
            v = v.replace("\\:", ":")
            if k.startswith("IP4.ADDRESS"):
                info["addr"].append(v)
            elif k.startswith("IP4.DNS"):
                info["dns"].append(v)
            else:
                info[k] = v
        method = dns_auto = ""
        if con and con != "--":
            rc, m = run(["nmcli", "-g", "ipv4.method,ipv4.ignore-auto-dns", "connection", "show", con])
            lines = m.splitlines() + ["", ""]
            method, dns_auto = lines[0], lines[1]
        speed = _sys(dev, "speed")
        duplex = _sys(dev, "duplex")
        result.append({
            "device": dev, "type": typ, "state": state, "connection": "" if con == "--" else con,
            "vendor": info.get("GENERAL.VENDOR", ""), "product": info.get("GENERAL.PRODUCT", ""),
            "mac": info.get("GENERAL.HWADDR", ""), "address": info["addr"][0] if info["addr"] else "",
            "gateway": info.get("IP4.GATEWAY", ""), "dns": info["dns"],
            "method": method or "auto", "dns_manual": dns_auto == "yes",
            "speed": f"{int(speed) // 1000} Gbit/s" if speed.isdigit() and int(speed) >= 1000
                     else (f"{speed} Mbit/s" if speed.isdigit() and int(speed) > 0 else ""),
            "duplex": duplex,
        })
    return result


def set_ipv4(connection, method, address="", gateway=""):
    """method: auto | manual. address like 192.168.1.42/24."""
    cmd = ["nmcli", "connection", "modify", connection, "ipv4.method", method]
    if method == "manual":
        cmd += ["ipv4.addresses", address, "ipv4.gateway", gateway]
    else:
        cmd += ["ipv4.addresses", "", "ipv4.gateway", ""]
    ok = run(cmd, sudo=True)[0] == 0
    return ok and run(["nmcli", "connection", "up", connection], timeout=20, sudo=True)[0] == 0


def set_dns(connection, servers):
    """servers: list of IPs, or empty for automatic (from DHCP)."""
    if servers:
        cmd = ["nmcli", "connection", "modify", connection, "ipv4.dns", " ".join(servers),
               "ipv4.ignore-auto-dns", "yes"]
    else:
        cmd = ["nmcli", "connection", "modify", connection, "ipv4.dns", "", "ipv4.ignore-auto-dns", "no"]
    ok = run(cmd, sudo=True)[0] == 0
    return ok and run(["nmcli", "connection", "up", connection], timeout=20, sudo=True)[0] == 0


def wifi_present():
    return any(a["type"] == "wifi" for a in adapters())


def wifi_enabled():
    rc, out = run(["nmcli", "radio", "wifi"])
    return out == "enabled"


def wifi_set(on):
    run(["nmcli", "radio", "wifi", "on" if on else "off"], sudo=True)


def wifi_list():
    rc, out = run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"], timeout=10)
    nets, seen = [], set()
    for line in out.splitlines():
        parts = line.rsplit(":", 2)
        if len(parts) != 3 or not parts[0] or parts[0] in seen:
            continue
        seen.add(parts[0])
        nets.append({"ssid": parts[0], "signal": int(parts[1] or 0), "security": parts[2] or "Open"})
    return nets


def wifi_connect(ssid, password):
    cmd = ["nmcli", "device", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    return run(cmd, timeout=30, sudo=True)[0] == 0


# ---------------------------------------------------------------- time & region
def timezones():
    """-> {region: [city, ...]} from the tz database (canonical zones only)."""
    zones = set()
    for tab in ("/usr/share/zoneinfo/zone1970.tab", "/usr/share/zoneinfo/zone.tab"):
        try:
            with open(tab) as f:
                for line in f:
                    if line.startswith("#") or not line.strip():
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 3:
                        zones.add(parts[2].strip())
        except OSError:
            continue
    by_region = {}
    for z in sorted(zones):
        region, _, city = z.partition("/")
        if city:
            by_region.setdefault(region, []).append(city)
    by_region["UTC"] = []
    return by_region


_ZONE_COUNTRIES = None


def zone_countries():
    """-> {zone: country name} from tzdata's zone.tab + iso3166.tab."""
    global _ZONE_COUNTRIES
    if _ZONE_COUNTRIES is None:
        names = {}
        try:
            with open("/usr/share/zoneinfo/iso3166.tab") as f:
                for line in f:
                    if not line.startswith("#") and "\t" in line:
                        code, name = line.rstrip("\n").split("\t", 1)
                        names[code] = name
        except OSError:
            pass
        result = {}
        for tab in ("/usr/share/zoneinfo/zone.tab", "/usr/share/zoneinfo/zone1970.tab"):
            try:
                with open(tab) as f:
                    for line in f:
                        if line.startswith("#"):
                            continue
                        parts = line.split("\t")
                        if len(parts) >= 3 and parts[2].strip() not in result:
                            code = parts[0].split(",")[0]
                            result[parts[2].strip()] = names.get(code, "")
            except OSError:
                continue
        _ZONE_COUNTRIES = result
    return _ZONE_COUNTRIES


def time_status():
    """-> dict(timezone, ntp, synced, server)."""
    rc, out = run(["timedatectl", "show", "-p", "Timezone", "-p", "NTP", "-p", "NTPSynchronized"])
    info = dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    rc, srv = run(["timedatectl", "show-timesync", "-p", "ServerName", "--value"])
    tz = info.get("Timezone", "")
    if not tz:
        try:
            tz = os.path.realpath("/etc/localtime").split("zoneinfo/", 1)[1]
        except (IndexError, OSError):
            tz = "UTC"
    return {"timezone": tz, "ntp": info.get("NTP") == "yes",
            "synced": info.get("NTPSynchronized") == "yes", "server": srv.strip()}


def set_timezone(tz):
    # litelink-state applies it and saves it for the next boot (persistent storage)
    ok = run(["/usr/local/sbin/litelink-state", "timezone", tz], timeout=15, sudo=True)[0] == 0
    if ok:
        import time
        os.environ.pop("TZ", None)
        time.tzset()          # the shell picks the new zone up immediately
    return ok


def set_ntp(on):
    # Saved for the next boot too (litelink-restore)
    return run(["/usr/local/sbin/litelink-state", "auto-time", "on" if on else "off"],
               timeout=15, sudo=True)[0] == 0


def maintenance_enabled():
    """False on an installed device where maintenance tools were left out."""
    return not os.path.exists("/run/litelink/no-maintenance")


def set_time(value):
    """value: 'YYYY-MM-DD HH:MM[:SS]' (only when automatic time is off)."""
    return run(["timedatectl", "set-time", value], sudo=True)[0] == 0


def ntp_servers():
    """-> custom servers (list), empty when using the defaults."""
    try:
        with open("/var/lib/litelink/ntp.conf") as f:
            for line in f:
                if line.startswith("NTP="):
                    return line[4:].split()
    except OSError:
        pass
    return []


def set_ntp_servers(servers):
    """Empty list restores the default pool."""
    return run(["/usr/local/sbin/litelink-state", "ntp", *servers], timeout=15, sudo=True)[0] == 0


# ---------------------------------------------------------------- storage & install
STORAGE = "/usr/local/sbin/litelink-storage"


def storage_status():
    """-> dict from `litelink-storage status` (empty dict if unavailable)."""
    import json
    rc, out = run([STORAGE, "status"], timeout=15, sudo=True)
    try:
        return json.loads(out) if rc == 0 else {}
    except ValueError:
        return {}


def storage_disks():
    import json
    rc, out = run([STORAGE, "disks"], timeout=15, sudo=True)
    try:
        return json.loads(out) if rc == 0 else []
    except ValueError:
        return []


def storage_run(args, on_progress, on_done, on_log=None):
    """Run `litelink-storage <args>` (install / usb-setup) without blocking the UI.
    on_progress(percent, message); on_done(ok, message); on_log(line) gets the
    detailed output (commands run and what they printed) and anything unexpected."""
    import threading
    from gi.repository import GLib

    def emit(text):
        if on_log:
            on_main(on_log, text)

    import time
    state = {"last": time.time(), "step": "starting", "running": True}

    def heartbeat():
        # A silent wait (e.g. a command that hangs) shows up in the log instead of a frozen screen
        while state["running"]:
            time.sleep(10)
            quiet = time.time() - state["last"]
            if state["running"] and quiet >= 30:
                emit(f"… still working on: {state['step']} (no output for {int(quiet)} s)")
                state["last"] = time.time()

    def worker():
        ok, msg = False, "The helper didn't finish"
        emit("$ sudo " + " ".join([STORAGE, *args]))
        threading.Thread(target=heartbeat, daemon=True).start()
        try:
            p = subprocess.Popen(["sudo", "-n", STORAGE, *args], stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in p.stdout:
                line = line.rstrip("\n")
                state["last"] = time.time()
                if line.startswith("LOG $ "):
                    state["step"] = line[6:]
                if line.startswith("PROGRESS "):
                    _, pct, *rest = line.split(" ", 2)
                    try:
                        on_main(on_progress, int(pct), rest[0] if rest else "")
                    except ValueError:
                        emit(line)
                elif line.startswith("LOG "):
                    emit(line[4:])
                elif line == "DONE":
                    ok, msg = True, ""
                elif line.startswith("ERROR "):
                    ok, msg = False, line[6:]
                    emit("ERROR " + line[6:])
                elif line.strip():
                    emit(line)                   # e.g. a sudo or Python error
            rc = p.wait()
            if not ok and not msg.strip():
                msg = f"The helper stopped (exit status {rc})"
            if rc != 0 and ok:
                ok, msg = False, f"The helper stopped (exit status {rc})"
        except OSError as e:
            msg = str(e)
            emit(f"couldn't start the helper: {e}")
        state["running"] = False
        on_main(on_done, ok, msg)
    threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------- USB drives (read-only)
MEDIA = "/usr/local/sbin/litelink-media"


def media_list():
    """USB drive partitions LiteLink can read: [{path, label, fstype, size, drive, mountpoint}]"""
    import json
    rc, out = run([MEDIA, "list"], timeout=10, sudo=True)
    try:
        return json.loads(out) if rc == 0 else []
    except ValueError:
        return []


def media_mount(dev):
    """Mount read-only; -> mount point or None."""
    rc, out = run([MEDIA, "mount", dev], timeout=20, sudo=True)
    return out if rc == 0 and out else None


def media_unmount(dev=None):
    run([MEDIA, "unmount"] + ([dev] if dev else []), timeout=20, sudo=True)


def find_files(root, suffix, depth=2, limit=40):
    """Files ending in suffix on a mounted drive -> [(path, folder label)]"""
    found = []
    root = root.rstrip("/")
    for cur, dirs, files in os.walk(root):
        level = cur[len(root):].count("/")
        dirs[:] = [d for d in sorted(dirs) if not d.startswith((".", "$", "System Volume"))] if level < depth else []
        for f in sorted(files):
            if f.lower().endswith(suffix) and not f.startswith("."):
                rel = cur[len(root):].strip("/")
                found.append((os.path.join(cur, f), f"{rel} folder" if rel else "Top folder"))
                if len(found) >= limit:
                    return found
    return found


# ---------------------------------------------------------------- VPN (WireGuard)
VPN = "/usr/local/sbin/litelink-vpn"


def _vpn(args, payload=None, timeout=40):
    """-> (ok, stdout, error message)"""
    import json
    try:
        r = subprocess.run(["sudo", "-n", VPN, *args], capture_output=True, text=True, timeout=timeout,
                           input=json.dumps(payload) if payload is not None else None)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, "", str(e)
    err = (r.stderr or "").strip()
    return r.returncode == 0, r.stdout.strip(), err[6:] if err.startswith("ERROR ") else err


def vpn_list():
    import json
    ok, out, _ = _vpn(["list"], timeout=15)
    try:
        return json.loads(out) if ok else []
    except ValueError:
        return []


def vpn_save(data):
    """Add or replace (data["uuid"]) a tunnel -> (ok, error message)"""
    ok, _out, err = _vpn(["save"], data)
    return ok, err


def vpn_remove(uuid):
    return _vpn(["remove", uuid])[0]


def vpn_set(uuid, up):
    ok, _out, err = _vpn(["up" if up else "down", uuid])
    return ok, err


def vpn_autoconnect(uuid, on):
    return _vpn(["autoconnect", uuid, "on" if on else "off"])[0]


def parse_wireguard(text):
    """A WireGuard .conf -> dict with the form's keys (first [Peer] only)."""
    d = {"private_key": "", "address": [], "dns": [], "public_key": "", "preshared_key": "",
         "endpoint": "", "allowed_ips": [], "keepalive": "", "peers": 0}
    section = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            if section == "peer":
                d["peers"] += 1
            continue
        if "=" not in line or (section == "peer" and d["peers"] > 1):
            continue
        k, v = (x.strip() for x in line.split("=", 1))
        k = k.lower()
        if section == "interface":
            if k == "privatekey":
                d["private_key"] = v
            elif k == "address":
                d["address"] += [a.strip() for a in v.split(",") if a.strip()]
            elif k == "dns":
                d["dns"] += [a.strip() for a in v.split(",") if a.strip()]
        elif section == "peer":
            if k == "publickey":
                d["public_key"] = v
            elif k == "presharedkey":
                d["preshared_key"] = v
            elif k == "endpoint":
                d["endpoint"] = v
            elif k == "allowedips":
                d["allowed_ips"] += [a.strip() for a in v.split(",") if a.strip()]
            elif k == "persistentkeepalive":
                d["keepalive"] = v
    for k in ("address", "dns", "allowed_ips"):
        d[k] = ", ".join(d[k])
    return d


def human_size(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}".replace(".0 ", " ")
        n /= 1000


def build_info():
    """Codename, build number (os-release) and install size (the OS image on
    the boot medium: kernel + initrd + squashfs)."""
    osr = {}
    try:
        for line in open("/etc/os-release"):
            k, _, v = line.strip().partition("=")
            osr[k] = v.strip('"')
    except OSError:
        pass
    size = 0      # exactly what the installer copies (the ISO also keeps versioned duplicates)
    for f in ("vmlinuz", "initrd.img", "filesystem.squashfs"):
        try:
            size += os.path.getsize(os.path.join("/run/live/medium/live", f))
        except OSError:
            pass
    ours = osr.get("ID") == "litelink"      # never show the Debian codename (trixie)
    return {"codename": ((osr.get("VERSION_CODENAME") if ours else "") or "siamese").title(),
            "build": osr.get("BUILD_ID", "") if ours else "", "size": size}


def is_installed():
    """True when running from an installed disk (its GRUB boots the LITELINK
    partition), False on a USB stick / DVD."""
    try:
        return "live-media=/dev/disk/by-label/LITELINK" in open("/proc/cmdline").read().split()
    except OSError:
        return False


def boot_option(name):
    try:
        return name in open("/proc/cmdline").read().split()
    except OSError:
        return False


# ---------------------------------------------------------------- bluetooth
_MAC = re.compile(r"Device ([0-9A-F:]{17}) (.*)")


def _btctl(*args, timeout=10):
    return run(["bluetoothctl", *args], timeout=timeout)


def bt_adapter():
    """-> dict(present, powered, name).
    Checks the kernel first: bluetoothctl blocks waiting for bluetoothd when
    there is no adapter or daemon, which would freeze the UI."""
    try:
        has_hci = any(n.startswith("hci") for n in os.listdir("/sys/class/bluetooth"))
    except OSError:
        has_hci = False
    if not has_hci or not have("bluetoothctl"):
        return {"present": False, "powered": False, "name": ""}
    rc, out = _btctl("show", timeout=3)
    if rc != 0 or "Controller" not in out:
        return {"present": False, "powered": False, "name": ""}
    name = re.search(r"Alias: (.*)", out)
    return {"present": True, "powered": "Powered: yes" in out, "name": name.group(1) if name else ""}


def bt_power(on):
    if on:
        run(["rfkill", "unblock", "bluetooth"], sudo=True)
    _btctl("power", "on" if on else "off")


def _devices(*filter_):
    rc, out = _btctl("devices", *filter_)
    return [(m.group(1), m.group(2).strip()) for m in map(_MAC.search, out.splitlines()) if m]


def bt_info(mac):
    rc, out = _btctl("info", mac)
    icon = re.search(r"Icon: (\S+)", out)
    return {"connected": "Connected: yes" in out, "paired": "Paired: yes" in out,
            "kind": (icon.group(1) if icon else "")}


def bt_paired():
    """-> [dict(mac, name, connected, kind)] for paired devices."""
    result = []
    for mac, name in _devices("Paired"):
        info = bt_info(mac)
        result.append({"mac": mac, "name": name, **info})
    return result


def bt_scan(seconds=8):
    """Discover nearby devices (blocking). -> [(mac, name)] not yet paired."""
    _btctl("--timeout", str(seconds), "scan", "on", timeout=seconds + 5)
    paired = {m for m, _ in _devices("Paired")}
    # Skip unnamed entries (BlueZ shows the address with dashes as the name)
    return [(m, n) for m, n in _devices() if m not in paired and n.replace("-", ":") != m]


def bt_pair(mac):
    """Pair, trust and connect. -> (ok, message)."""
    rc, out = _btctl("--timeout", "20", "pair", mac, timeout=25)
    if "Pairing successful" not in out and "AlreadyExists" not in out:
        err = re.search(r"Failed to pair: (\S+)", out)
        return False, (err.group(1).split(".")[-1] if err else "Pairing failed")
    _btctl("trust", mac)
    _btctl("--timeout", "10", "connect", mac, timeout=15)
    return True, "Paired"


def bt_connect(mac, on=True):
    _btctl("--timeout", "10", "connect" if on else "disconnect", mac, timeout=15)


def bt_remove(mac):
    _btctl("remove", mac)


def on_main(fn, *args):
    """Run fn(*args) on the GTK main loop, from any thread, at normal priority.
    Plain GLib.idle_add uses the lowest priority: on a slow CPU (Wyse 3040's Atom)
    an animation plus its redraws keep the loop busy and such callbacks never run
    (the installer looked stuck at "Starting…" while it was working)."""
    from gi.repository import GLib

    def call():
        fn(*args)
        return False
    GLib.idle_add(call, priority=GLib.PRIORITY_DEFAULT)


def in_background(work, done):
    """Run work() on a thread, then done(result) on the GTK main loop."""
    import threading

    def runner():
        result = work()
        on_main(done, result)
    threading.Thread(target=runner, daemon=True).start()


# ---------------------------------------------------------------- audio
def volume_get():
    rc, out = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])
    m = re.search(r"Volume:\s*([\d.]+)", out)
    return (int(float(m.group(1)) * 100) if m else 0), ("MUTED" in out)


def volume_set(pct):
    run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{max(0, min(100, pct))}%"])


def mute_set(on):
    run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "1" if on else "0"])


def sink_name():
    rc, out = run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"])
    m = re.search(r'node\.description = "([^"]+)"', out)
    return m.group(1) if m else "Default output"


# ---------------------------------------------------------------- display
def displays():
    """-> list of dict(output, current, modes=[...], rate) for connected outputs."""
    rc, out = run(["xrandr", "--query"])
    result, cur = [], None
    for line in out.splitlines():
        m = re.match(r"^(\S+) connected", line)
        if m:
            cur = {"output": m.group(1), "current": "", "rate": "", "modes": []}
            result.append(cur)
            continue
        if re.match(r"^\S", line):
            cur = None
            continue
        m = re.match(r"^\s+(\d+x\d+)\s+(.*)$", line)
        if cur is not None and m:
            mode, rates = m.group(1), m.group(2)
            if mode not in cur["modes"]:
                cur["modes"].append(mode)
            if "+" in rates and "preferred" not in cur:
                cur["preferred"] = mode
            if "*" in rates:
                cur["current"] = mode
                r = re.search(r"([\d.]+)\*", rates)
                cur["rate"] = r.group(1) if r else ""
    return result


def set_mode(output, mode):
    return run(["xrandr", "--output", output, "--mode", mode])[0] == 0


# ---------------------------------------------------------------- power
def power(action):
    """action: poweroff | reboot"""
    run(["systemctl", action], sudo=True)


# ---------------------------------------------------------------- about
def os_release():
    info = {}
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if "=" in line:
                    k, v = line.rstrip().split("=", 1)
                    info[k] = v.strip('"')
    except OSError:
        pass
    return info


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return ""


def about():
    osr = os_release()
    cpu = ""
    for line in _read("/proc/cpuinfo").splitlines():
        if line.startswith("model name"):
            cpu = re.sub(r"\s+", " ", line.split(":", 1)[1]).strip()
            break
    mem_kb = 0
    m = re.search(r"MemTotal:\s+(\d+)", _read("/proc/meminfo"))
    if m:
        mem_kb = int(m.group(1))
    vendor = _read("/sys/class/dmi/id/sys_vendor")
    product = _read("/sys/class/dmi/id/product_name")
    rc, fr = run(["xfreerdp3", "/version"])
    frv = re.search(r"version ([\d.]+)", fr)
    deb = _read("/etc/debian_version")
    return {
        "name": osr.get("PRETTY_NAME", "LiteLink OS"),
        "rows": [
            ("Device", " ".join(x for x in (vendor, product) if x) or "Unknown"),
            ("Processor", cpu or platform.machine()),
            ("Memory", f"{round(mem_kb / 1048576, 1):g} GB" if mem_kb else "Unknown"),
            ("Base system", f"Debian {deb} · Linux {platform.release().split('-')[0]}"),
            ("RDP client", f"FreeRDP {frv.group(1)}" if frv else "FreeRDP 3"),
            ("Hostname", socket.gethostname()),
            ("IP address", primary_ip() or "Not connected"),
        ],
    }


def have(tool):
    return shutil.which(tool) is not None
