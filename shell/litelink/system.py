"""Thin wrappers around system tools: NetworkManager, PipeWire, xrandr, power."""
import glob
import json
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


def network_profiles():
    """-> [(name, active)] for every saved Ethernet and Wi-Fi connection."""
    rc, out = run(["nmcli", "-t", "-f", "NAME,TYPE,ACTIVE", "connection", "show"])
    result = []
    for line in out.splitlines():
        parts = line.replace("\\:", "\0").split(":")
        if len(parts) == 3 and parts[1] in ("802-3-ethernet", "802-11-wireless"):
            result.append((parts[0].replace("\0", ":"), parts[2] == "yes"))
    return result


def set_dns_all(servers):
    """DNS for the whole device: every saved Ethernet and Wi-Fi connection,
    so it stays the same whichever adapter or network is in use."""
    ok = True
    for name, active in network_profiles():
        if active:
            ok = set_dns(name, servers) and ok
        else:
            args = (["ipv4.dns", " ".join(servers), "ipv4.ignore-auto-dns", "yes"] if servers
                    else ["ipv4.dns", "", "ipv4.ignore-auto-dns", "no"])
            ok = run(["nmcli", "connection", "modify", name, *args], sudo=True)[0] == 0 and ok
    return ok


def wifi_present():
    return any(a["type"] == "wifi" for a in adapters())


def wifi_enabled():
    rc, out = run(["nmcli", "radio", "wifi"])
    return out == "enabled"


def wifi_set(on):
    run(["nmcli", "radio", "wifi", "on" if on else "off"], sudo=True)


def wifi_list():
    """Nearby networks, the connected one first (it has 'active': True)."""
    rc, out = run(["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list"], timeout=10)
    nets = {}
    for line in out.splitlines():
        in_use, _, rest = line.partition(":")
        parts = rest.rsplit(":", 2)
        if len(parts) != 3 or not parts[0]:
            continue
        ssid = parts[0].replace("\\:", ":")
        n = {"ssid": ssid, "signal": int(parts[1] or 0), "security": parts[2] or "Open",
             "active": in_use.strip() == "*"}
        old = nets.get(ssid)          # one row per name, even with several access points
        if old is None:
            nets[ssid] = n
        else:
            old["active"] = old["active"] or n["active"]
            old["signal"] = max(old["signal"], n["signal"])
    return sorted(nets.values(), key=lambda n: (not n["active"], -n["signal"]))


def wifi_current():
    """The Wi-Fi network this device is connected to ('' if none), even when
    Ethernet is the primary connection."""
    rc, out = run(["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"], timeout=10)
    for line in out.splitlines():
        if line.startswith("yes:"):
            return line[4:]
    return ""


def wifi_connect(ssid, password="", auto=True):
    """Join a network (a saved one uses its stored password when none is given).
    auto: connect to it by itself from now on. -> (ok, reason), reason one of
    '' | 'password' | 'notfound' | 'timeout' | 'failed'."""
    cmd = ["sudo", "-n", "nmcli", "--wait", "30", "device", "wifi", "connect", ssid]
    if password:
        cmd += ["password", password]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=40)
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except OSError:
        return False, "failed"
    if p.returncode != 0:
        err = (p.stderr + p.stdout).lower()
        if "secrets were required" in err or "psk" in err or "password" in err or "802-1x" in err:
            return False, "password"
        if "no network with ssid" in err:
            return False, "notfound"
        if "timeout" in err or "timed out" in err:
            return False, "timeout"
        return False, "failed"
    rc, out = run(["nmcli", "-t", "-f", "UUID,TYPE", "connection", "show", "--active"])
    for line in out.splitlines():
        uuid, _, typ = line.partition(":")
        if typ == "802-11-wireless":
            run(["nmcli", "connection", "modify", uuid, "connection.autoconnect", "yes" if auto else "no"],
                sudo=True)
            break
    return True, ""


def wifi_state():
    """The Wi-Fi adapter's NetworkManager state, e.g. 'connecting (need authentication)'."""
    rc, out = run(["nmcli", "-t", "-f", "TYPE,STATE", "device"])
    for line in out.splitlines():
        typ, _, state = line.partition(":")
        if typ == "wifi":
            return state
    return ""


def wifi_saved():
    """Names of the saved Wi-Fi connections (NetworkManager names them after the network)."""
    rc, out = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"])
    return {line.rpartition(":")[0].replace("\\:", ":") for line in out.splitlines()
            if line.endswith(":802-11-wireless")}


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


def vpn_active():
    """Names of the WireGuard tunnels that are up (cheap: no sudo, for polling)."""
    rc, out = run(["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show", "--active"])
    return [l.rsplit(":", 1)[0].replace("\\:", ":") for l in out.splitlines() if l.endswith(":wireguard")]


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


def bt_connected():
    """Names of connected Bluetooth devices ([] without an adapter, never blocking on one)."""
    ad = bt_adapter()
    if not ad["powered"]:
        return []
    return [name for _mac, name in _devices("Connected")]


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


_WP_NODE = re.compile(r"^(\*)?\s*(\d+)\.\s+(.+?)\s*(?:\[vol: ([\d.]+)( MUTED)?\])?\s*$")


def audio_devices():
    """-> {"sinks": [...], "sources": [...]}, each dict(id, name, default, volume, muted),
    from `wpctl status` (the Audio section only)."""
    rc, out = run(["wpctl", "status"])
    result = {"sinks": [], "sources": []}
    section = audio = None
    for raw in out.splitlines():
        if raw and not raw[0].isspace() and raw[0] not in "│├└":
            audio = raw.strip() == "Audio"           # Audio / Video / Settings blocks
            section = None
            continue
        if not audio:
            continue
        line = raw.strip(" │├└─\t")
        if line.endswith(":"):
            section = {"Sinks:": "sinks", "Sources:": "sources"}.get(line)
            continue
        if section and line:
            m = _WP_NODE.match(line)
            if m:
                result[section].append({"id": m.group(2), "name": m.group(3), "default": bool(m.group(1)),
                                        "volume": int(float(m.group(4)) * 100) if m.group(4) else 100,
                                        "muted": bool(m.group(5))})
    return result


def audio_default(node_id):
    run(["wpctl", "set-default", str(node_id)])


def source_volume_get():
    rc, out = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SOURCE@"])
    m = re.search(r"Volume:\s*([\d.]+)", out)
    return (int(float(m.group(1)) * 100) if m else 0), ("MUTED" in out)


def source_volume_set(pct):
    run(["wpctl", "set-volume", "@DEFAULT_AUDIO_SOURCE@", f"{max(0, min(100, pct))}%"])


def source_mute_set(on):
    run(["wpctl", "set-mute", "@DEFAULT_AUDIO_SOURCE@", "1" if on else "0"])


def play_test_sound():
    """A short two-note chime on the default output (blocking, ~1 s)."""
    import math
    import struct
    import wave
    path = "/tmp/litelink-test-sound.wav"
    if not os.path.exists(path):
        rate = 44100
        frames = bytearray()
        for freq, dur in ((880, 0.18), (1320, 0.32)):
            n = int(rate * dur)
            for i in range(n):
                env = min(1.0, i / 400, (n - i) / 2000)
                v = int(9000 * env * math.sin(2 * math.pi * freq * i / rate))
                frames += struct.pack("<hh", v, v)
        with wave.open(path, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(bytes(frames))
    return run(["pw-play", path], timeout=10)[0] == 0


# ---------------------------------------------------------------- keyboard & input
XKB_RULES = "/usr/share/X11/xkb/rules/evdev.lst"


def keyboard_layouts():
    """-> ([(layout, description)], {layout: [(variant, description)]}) from the XKB rules."""
    layouts, variants, section = [], {}, None
    try:
        with open(XKB_RULES, encoding="utf-8") as f:
            for line in f:
                if line.startswith("!"):
                    section = line[1:].strip()
                    continue
                parts = line.strip().split(None, 1)
                if len(parts) != 2:
                    continue
                if section == "layout":
                    layouts.append((parts[0], parts[1]))
                elif section == "variant":
                    lay, _, desc = parts[1].partition(": ")
                    variants.setdefault(lay, []).append((parts[0], desc or parts[1]))
    except OSError:
        pass
    layouts.sort(key=lambda x: x[1].lower())
    return layouts, variants


def keyboard_current():
    rc, out = run(["setxkbmap", "-query"])
    q = dict((k.strip(), v.strip()) for k, _, v in (l.partition(":") for l in out.splitlines()))
    return q.get("layout", "us").split(",")[0], q.get("variant", "").split(",")[0]


def pointer_devices():
    """-> [dict(id, name, touchpad)] for the pointing devices (not the virtual core ones)."""
    rc, out = run(["xinput", "list"])
    devs = []
    for line in out.splitlines():
        m = re.search(r"[↳ ]*(.+?)\s+id=(\d+)\s+\[slave\s+pointer", line)
        if m and "XTEST" not in m.group(1):
            name = m.group(1).strip("⎜↳ \t")
            devs.append({"id": m.group(2), "name": name, "touchpad": None})
    for d in devs:
        rc, props = run(["xinput", "list-props", d["id"]])
        d["touchpad"] = "libinput Tapping Enabled (" in props
        d["props"] = props
    return devs


def apply_input(s):
    """Apply Settings › Keyboard & input (s = cfg.data['input']). Safe to call
    again: new mice and keyboards are picked up the next time it runs."""
    if s.get("layout"):
        cmd = ["setxkbmap", "-layout", s["layout"]]
        if s.get("variant"):
            cmd += ["-variant", s["variant"]]
        run(cmd)
    run(["xset", "r", "rate", str(int(s.get("repeat_delay", 500))), str(int(s.get("repeat_rate", 25)))])
    for d in pointer_devices():
        def prop(name, *vals):
            if f"{name} (" in d["props"]:
                run(["xinput", "set-prop", d["id"], name, *map(str, vals)])
        prop("libinput Accel Speed", round(float(s.get("pointer_speed", 0.0)), 2))
        prop("libinput Left Handed Enabled", int(bool(s.get("left_handed"))))
        if d["touchpad"]:
            prop("libinput Tapping Enabled", int(bool(s.get("tap_to_click", True))))
            prop("libinput Natural Scrolling Enabled", int(bool(s.get("natural_scroll"))))


def input_device_ids():
    return run(["xinput", "list", "--id-only"])[1]


# ---------------------------------------------------------------- diagnostics
def ping(host, timeout=2.0):
    """ICMP echo without root (unprivileged ICMP sockets).
    -> milliseconds, None (no answer) or False (pings aren't allowed here)."""
    import time
    try:
        addr = socket.getaddrinfo(host, None, socket.AF_INET)[0][4][0]
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_ICMP)
    except PermissionError:
        return False
    except OSError:
        return None
    try:
        s.settimeout(timeout)
        body = b"litelink"
        t0 = time.monotonic()
        s.sendto(_icmp_header(body) + body, (addr, 0))
        s.recv(1024)
        return round((time.monotonic() - t0) * 1000)
    except OSError:
        return None
    finally:
        s.close()


def _icmp_header(body):
    """Echo request header with its checksum (the kernel fills in the id)."""
    import struct
    hdr = struct.pack("!BBHHH", 8, 0, 0, 0, 1)
    data = hdr + body
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    total = (total >> 16) + (total & 0xFFFF)
    total += total >> 16
    return struct.pack("!BBHHH", 8, 0, ~total & 0xFFFF, 0, 1)


def tcp_check(host, port, timeout=4.0):
    """-> (ok, milliseconds or error text)"""
    import time
    t0 = time.monotonic()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True, round((time.monotonic() - t0) * 1000)
    except socket.gaierror:
        return False, "name not found"
    except socket.timeout:
        return False, "no answer"
    except ConnectionRefusedError:
        return False, "refused"
    except OSError as e:
        return False, (e.strerror or str(e)).lower()


def resolve(host):
    """-> (ok, first address or error text)"""
    try:
        return True, socket.getaddrinfo(host, None)[0][4][0]
    except OSError:
        return False, "name not found"


def device_health():
    """Uptime, memory and storage rows for Diagnostics."""
    rows = []
    try:
        up = float(_read("/proc/uptime").split()[0])
        d, h, m = int(up // 86400), int(up % 86400 // 3600), int(up % 3600 // 60)
        rows.append(("Uptime", (f"{d} d " if d else "") + f"{h} h {m} min"))
    except (ValueError, IndexError):
        pass
    mem = dict((l.split(":")[0], int(l.split()[1])) for l in _read("/proc/meminfo").splitlines()
               if len(l.split()) >= 2 and l.split()[1].isdigit())
    if mem.get("MemTotal"):
        used = mem["MemTotal"] - mem.get("MemAvailable", 0)
        rows.append(("Memory in use", f"{human_size(used * 1024)} of {human_size(mem['MemTotal'] * 1024)}"))
    if mem.get("SwapTotal"):
        used = mem["SwapTotal"] - mem.get("SwapFree", 0)
        rows.append(("Compressed swap", f"{human_size(used * 1024)} of {human_size(mem['SwapTotal'] * 1024)}"))
    for path, label in (("/var/lib/litelink", "Settings storage"), ("/", "System")):
        try:
            st = os.statvfs(path)
            total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
            if total:
                rows.append((label, f"{human_size(total - free)} used of {human_size(total)}"))
                break
        except OSError:
            pass
    load = _read("/proc/loadavg").split()[:3]
    if load:
        rows.append(("Load (1, 5, 15 min)", ", ".join(load)))
    return rows


_SECRET = re.compile(r"((?:^|[\s,])(?:password|passwd|psk|wep-key\d|private-key-password|"
                     r"802-1x\.password|802-11-wireless-security\.psk|/p)[\s:=]+)\S+", re.I)


def redact(text):
    """Hide secrets that commands may have put in a log line (e.g. an old sudo
    entry for `nmcli device wifi connect X password Y`)."""
    return _SECRET.sub(lambda m: m.group(1) + "[hidden]", text)


def recent_log(lines=300):
    """This boot's system log (journal) and the shell's own log, newest last,
    with passwords blanked out."""
    rc, out = run(["sudo", "-n", "journalctl", "-b", "-n", str(lines), "--no-pager", "-q", "-o", "short"],
                  timeout=10)
    shell = ""
    try:
        with open("/tmp/litelink-shell.log", errors="replace") as f:
            shell = "".join(f.readlines()[-80:])
    except OSError:
        pass
    return redact(out), redact(shell)


def save_to_usb(dev, name, text):
    """Write a text file to a USB drive (mounted writable just for this). -> (ok, error)"""
    try:
        r = subprocess.run(["sudo", "-n", MEDIA, "save", dev, name], input=text, capture_output=True,
                           text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    err = (r.stderr or "").strip()
    return r.returncode == 0, err[6:] if err.startswith("ERROR ") else err


# ---------------------------------------------------------------- 802.1X (wired)
def dot1x_get(connection):
    """-> dict(eap, identity, anonymous, ca_cert, client_cert, private_key) ('' = off)."""
    rc, out = run(["nmcli", "-g", "802-1x.eap,802-1x.identity,802-1x.anonymous-identity,802-1x.ca-cert,"
                   "802-1x.client-cert,802-1x.private-key", "connection", "show", connection])
    vals = (out.splitlines() + [""] * 6)[:6]
    keys = ("eap", "identity", "anonymous", "ca_cert", "client_cert", "private_key")
    d = {k: v.replace("\\:", ":").strip() for k, v in zip(keys, vals)}
    for k in ("ca_cert", "client_cert", "private_key"):
        d[k] = d[k].replace("file://", "")
    d["eap"] = d["eap"].split(",")[0]
    return d


def dot1x_set(connection, s):
    """s: dict(eap: ''|peap|ttls|tls, identity, password, anonymous, ca_cert,
    client_cert, private_key, key_password). '' turns 802.1X off. -> ok"""
    if not s.get("eap"):
        cmd = ["nmcli", "connection", "modify", connection, "remove", "802-1x"]
        ok = run(cmd, sudo=True)[0] == 0
    else:
        args = ["802-1x.eap", s["eap"], "802-1x.identity", s.get("identity", ""),
                "802-1x.anonymous-identity", s.get("anonymous", ""),
                "802-1x.ca-cert", s.get("ca_cert", "")]
        if s["eap"] == "tls":
            args += ["802-1x.client-cert", s.get("client_cert", ""), "802-1x.private-key", s.get("private_key", ""),
                     "802-1x.private-key-password", s.get("key_password", ""), "802-1x.phase2-auth", ""]
        else:
            args += ["802-1x.phase2-auth", "mschapv2", "802-1x.client-cert", "", "802-1x.private-key", ""]
            if "password" in s:               # absent: keep the saved password
                args += ["802-1x.password", s["password"]]
        ok = run(["nmcli", "connection", "modify", connection, *args], sudo=True)[0] == 0
    return ok and run(["nmcli", "connection", "up", connection], timeout=30, sudo=True)[0] == 0


# ---------------------------------------------------------------- automatic time zone
TZ_LOOKUPS = ("https://ipapi.co/timezone/", "http://ip-api.com/line/?fields=timezone")


def physical_uplink():
    """The hardware adapter (Ethernet, Wi-Fi, USB modem) the main default route
    goes out of, or '' when there's none. VPN tunnels have no hardware behind
    them (/sys/class/net/DEV/device), so they're never picked."""
    for fam in ("-4", "-6"):
        rc, out = run(["ip", fam, "route", "show", "table", "main", "default"])
        for line in out.splitlines():
            m = re.search(r"\bdev (\S+)", line)
            if m and os.path.exists(f"/sys/class/net/{m.group(1)}/device"):
                return m.group(1)
    return ""


def _fetch_via(url, dev, timeout=6):
    """GET url with the socket bound to network adapter dev (SO_BINDTODEVICE:
    allowed without root for a fresh socket since Linux 5.7). A bound socket
    skips a VPN's catch-all route, so the request leaves through dev itself."""
    import http.client
    import socket
    import ssl
    import urllib.parse
    u = urllib.parse.urlsplit(url)
    https = u.scheme == "https"
    port = u.port or (443 if https else 80)
    sock, err = None, OSError("no address")
    for fam, typ, proto, _c, addr in socket.getaddrinfo(u.hostname, port, type=socket.SOCK_STREAM):
        s = socket.socket(fam, typ, proto)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BINDTODEVICE, dev.encode())
            s.settimeout(timeout)
            s.connect(addr)
            sock = s
            break
        except OSError as e:
            err = e
            s.close()
    if sock is None:
        raise err
    if https:
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=u.hostname)
    cls = http.client.HTTPSConnection if https else http.client.HTTPConnection   # right Host header
    conn = cls(u.hostname, port, timeout=timeout)
    conn.sock = sock                    # already connected (and encrypted): use it as is
    try:
        conn.request("GET", (u.path or "/") + (f"?{u.query}" if u.query else ""),
                     headers={"User-Agent": "LiteLink", "Connection": "close"})
        r = conn.getresponse()
        if r.status != 200:
            raise OSError(f"HTTP {r.status}")
        return r.read(100)
    finally:
        conn.close()


def lookup_timezone():
    """The time zone for this device's public IP address ('' if unknown).
    The request goes out through the hardware adapter, never a VPN tunnel: a
    tunnel's exit point may be in another country."""
    dev = physical_uplink()
    if not dev:
        return ""                       # no network
    for url in TZ_LOOKUPS:
        try:
            tz = _fetch_via(url, dev).decode("ascii", "replace").strip()
        except Exception:  # noqa: BLE001 - any failure: try the next service
            continue
        if re.fullmatch(r"[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*", tz) and os.path.exists(f"/usr/share/zoneinfo/{tz}"):
            return tz
    return ""


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


def print_engine():
    """The print system (CUPS) is on this device. It's an image module
    (print.squashfs): always on the USB stick, on an installed disk only when
    Printers was ticked in the installer."""
    return os.path.exists("/usr/sbin/cupsd")


# CUPS is asked over IPP (its own protocol, on localhost:631), not with lpstat / lpoptions: those list
# destinations through libcups, which also browses the network for printers for about a second on
# every call; a direct request takes a few milliseconds.
_IPP_TEXT = range(0x30, 0x50)        # text, name, keyword, uri, charset, language, mime type


def _ipp(op, attrs=(), uri="ipp://localhost/", path="/"):
    """One IPP request to CUPS. attrs: [(value tag, name, value or [values])].
    -> [{name: value or [values]}] for the printer / job groups of the answer (None if CUPS can't be asked)."""
    import http.client
    import struct

    def attr(tag, name, value):
        out = b""
        for i, v in enumerate(value if isinstance(value, list) else [value]):
            v = v.encode() if isinstance(v, str) else v
            n = name.encode() if i == 0 else b""
            out += struct.pack(">BH", tag, len(n)) + n + struct.pack(">H", len(v)) + v
        return out
    body = struct.pack(">BBHI", 2, 0, op, 1) + b"\x01"
    body += attr(0x47, "attributes-charset", "utf-8") + attr(0x48, "attributes-natural-language", "en")
    body += attr(0x45, "printer-uri", uri)
    for tag, name, value in attrs:
        body += attr(tag, name, value)
    body += b"\x03"
    try:
        conn = http.client.HTTPConnection("localhost", 631, timeout=5)
        conn.request("POST", path, body, {"Content-Type": "application/ipp"})
        data = conn.getresponse().read()
        conn.close()
    except (OSError, http.client.HTTPException):
        return None
    if len(data) < 8:
        return None
    if struct.unpack(">H", data[2:4])[0] >= 0x0400:      # a client or server error (no printers: not found)
        return []
    groups, cur, last, i = [], None, None, 8
    try:
        while i < len(data):
            tag = data[i]
            i += 1
            if tag == 0x03:
                break
            if tag < 0x10:                                   # a new group: printers (4) and jobs (2) are kept
                cur = {} if tag in (0x02, 0x04) else None
                if cur is not None:
                    groups.append(cur)
                continue
            nlen = struct.unpack(">H", data[i:i + 2])[0]
            name = data[i + 2:i + 2 + nlen].decode(errors="replace")
            i += 2 + nlen
            vlen = struct.unpack(">H", data[i:i + 2])[0]
            raw = data[i + 2:i + 2 + vlen]
            i += 2 + vlen
            if tag in (0x21, 0x23) and vlen == 4:            # integer, enum
                value = struct.unpack(">i", raw)[0]
            elif tag == 0x22:                                # boolean
                value = raw == b"\x01"
            elif tag in _IPP_TEXT:
                value = raw.decode(errors="replace")
            else:
                value = raw
            if cur is None:
                continue
            if nlen:
                last = name
                cur[name] = value
            elif last in cur:                                # another value of the same attribute
                cur[last] = (cur[last] if isinstance(cur[last], list) else [cur[last]]) + [value]
    except (IndexError, struct.error):
        pass
    return groups


def _listed(v):
    return v if isinstance(v, list) else [] if v is None else [v]


def _printer_message(p, state):
    """What CUPS says about a printer, as a sentence: its message (when it says something), else its
    alerts ("media-empty-error" -> "Media empty"). "Idle." / "Ready to print." linger from before a
    job, and CUPS's own reasons (cups-waiting-for-job-completed) say nothing."""
    text = (p.get("printer-state-message") or "").strip()
    if state != "idle" and text and text not in ("Idle.", "Ready to print."):
        return text
    reasons = [r for r in _listed(p.get("printer-state-reasons"))
               if isinstance(r, str) and r != "none" and not r.startswith("cups-")]
    words = [re.sub(r"-(error|warning|report)$", "", r).replace("-", " ") for r in reasons]
    return ", ".join(words).capitalize()


def printers():
    """Printers set up on this device (Settings › Printers), from CUPS:
    -> [dict(name, info, uri, kind 'usb'|'network', state 'idle'|'printing'|'stopped', message, default)]."""
    got = _ipp(0x4002, [(0x44, "requested-attributes",
                         ["printer-name", "printer-info", "printer-state", "printer-state-message",
                          "printer-state-reasons", "device-uri"])]) or []
    default = next((g.get("printer-name") for g in _ipp(0x4001) or []), None)
    found = []
    for g in got:
        name = g.get("printer-name")
        if not isinstance(name, str) or "@" in name:       # another computer's printer (name@host), not set up here
            continue
        state = {4: "printing", 5: "stopped"}.get(g.get("printer-state"), "idle")
        uri = g.get("device-uri") if isinstance(g.get("device-uri"), str) else ""
        found.append({"name": name, "info": (g.get("printer-info") or "").strip() or name, "uri": uri,
                      "kind": "usb" if _usb_uri(uri) else "network", "state": state,
                      "message": _printer_message(g, state), "default": name == default})
    # by name: the default changes with every print (the printer used last), rows mustn't jump
    return sorted(found, key=lambda p: p["info"].lower())


def print_jobs():
    """Documents not finished yet, any session's: -> [dict(job 'Printer-12', printer, state
    'pending'|'printing'|'stopped')], or None if CUPS can't be asked."""
    got = _ipp(0x000A, [(0x44, "which-jobs", "not-completed"),
                        (0x44, "requested-attributes", ["job-id", "job-state", "job-printer-uri"])])
    if got is None:
        return None
    jobs = []
    for g in got:
        printer = (g.get("job-printer-uri") or "").rstrip("/").rsplit("/", 1)[-1]
        if isinstance(g.get("job-id"), int) and printer:
            jobs.append({"job": f"{printer}-{g['job-id']}", "printer": printer,
                         "state": {5: "printing", 4: "stopped", 6: "stopped"}.get(g.get("job-state"), "pending")})
    return jobs


def printer_caps(name):
    """What a printer offers, from the description CUPS keeps for it (its PPD, served on localhost):
    -> {duplex, color, media: [...], mediaDefault}."""
    import urllib.parse
    import urllib.request
    try:
        with urllib.request.urlopen("http://localhost:631/printers/%s.ppd" % urllib.parse.quote(name),
                                    timeout=5) as r:
            ppd = r.read(512 * 1024).decode("utf-8", "replace")
    except (OSError, ValueError):
        ppd = ""

    def choices(key):
        return re.findall(r"(?m)^\*%s ([^\s/:]+)" % key, ppd)

    def default(key):
        m = re.search(r"(?m)^\*Default%s: (\S+)" % key, ppd)
        return m.group(1) if m else ""
    sizes = [x for x in choices("PageSize") if not x.startswith("Custom")]
    colour = choices("ColorModel") + choices("print-color-mode")
    media_default = default("PageSize")
    return {"duplex": any(c.startswith("Duplex") for c in choices("Duplex")),
            "color": any(c.lower() not in ("gray", "grey", "monochrome", "black", "kgray", "bi-level")
                         for c in colour),
            "media": sizes[:40], "mediaDefault": "" if media_default.startswith("Custom") else media_default}


def _usb_uri(uri):
    """A USB printer: usb://, or one ipp-usb serves on this device (localhost, ports 60000-60099)."""
    return uri.startswith("usb:") or bool(re.match(r"ipps?://(localhost|127\.0\.0\.1):600\d\d/", uri))


def _print_helper(args, timeout=120):
    """-> (ok, stdout or the helper's sentence)."""
    try:
        r = subprocess.run(["sudo", "-n", "/usr/local/sbin/litelink-print"] + args, capture_output=True, text=True,
                           timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return False, "The printer didn’t answer in time."
    if r.returncode:
        return False, (r.stderr.strip().splitlines() or ["Something went wrong with the printer."])[-1]
    return True, r.stdout


def printer_find():
    """Printers found now (USB and network). -> (ok, [dict(uri, name, kind, driverless)] or message)."""
    ok, out = _print_helper(["find"], timeout=60)
    if not ok:
        return False, out
    try:
        return True, json.loads(out or "[]")
    except ValueError:
        return False, "Couldn’t look for printers."


def printer_name(info, taken=()):
    """A CUPS queue name from a printer's name: letters, digits, _ . - only, not one already used."""
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", info).strip("_.-")[:56] or "Printer"
    name, n = base, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    return name


def printer_add(name, uri, info=""):
    """-> (ok, the name it's shown under, or the helper's sentence). Without info (added by address)
    the helper names it after the printer's own model."""
    ok, out = _print_helper(["add", name, uri] + ([info] if info else []))
    if not ok:
        return False, out
    try:
        return True, json.loads(out).get("info") or info or name
    except ValueError:
        return True, info or name


def printer_remove(name):
    return _print_helper(["remove", name], timeout=30)


def printer_default(name):
    return _print_helper(["default", name], timeout=30)


def printer_last():
    """The printer used last becomes the default (run when a web session ends: that catches
    Chromium's own print dialog, which the device bridge doesn't see)."""
    return _print_helper(["last"], timeout=30)


def printer_test(name):
    ok, out = _print_helper(["test", name], timeout=30)
    return ok, ("" if ok else out)


def web_engine():
    """The browser engine (Chromium) is on this device. It's an image module
    (web.squashfs): always on the USB stick, on an installed disk only when Web
    kiosk was ticked in the installer."""
    return os.path.exists("/usr/lib/chromium/chromium")


def memory_kb():
    """Total memory (kB) as the kernel reports it, or 0 if unknown."""
    m = re.search(r"MemTotal:\s+(\d+)", _read("/proc/meminfo"))
    return int(m.group(1)) if m else 0


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


# ---------------------------------------------------------------- certificates (Settings › Certificates)
CA_DIR = os.path.expanduser("~/.config/litelink/certs/ca")     # kept with the settings


def _decode_cert(pem):
    """PEM text -> dict(subject, issuer, expires, fingerprint) or None."""
    import hashlib
    import ssl
    import tempfile
    try:
        der = ssl.PEM_cert_to_DER_cert(pem)
        with tempfile.NamedTemporaryFile("w", suffix=".pem") as f:
            f.write(ssl.DER_cert_to_PEM_cert(der))
            f.flush()
            info = ssl._ssl._test_decode_cert(f.name)
    except (ValueError, ssl.SSLError, OSError):
        return None

    def name(rdns):
        parts = dict(x[0] for x in rdns)
        return parts.get("commonName") or parts.get("organizationName") or parts.get("organizationalUnitName") or ""
    return {"subject": name(info.get("subject", ())), "issuer": name(info.get("issuer", ())),
            "expires": info.get("notAfter", ""), "fingerprint": hashlib.sha256(der).hexdigest()}


def ca_list():
    """The certificate authorities added in Settings: [dict(subject, issuer, expires, fingerprint, path)]."""
    out = []
    try:
        names = sorted(os.listdir(CA_DIR))
    except OSError:
        return out
    for n in names:
        path = os.path.join(CA_DIR, n)
        try:
            info = _decode_cert(open(path).read())
        except OSError:
            info = None
        if info:
            out.append(dict(info, path=path))
    return out


def ca_add(src):
    """A .pem / .crt / .cer / .der file -> (ok, message). Kept as PEM, named by its fingerprint."""
    import ssl
    try:
        raw = open(src, "rb").read(200000)
    except OSError:
        return False, "The file couldn\u2019t be read."
    text = raw.decode("ascii", "ignore")
    pems = re.findall(r"-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----", text, re.S)
    if not pems:
        try:
            pems = [ssl.DER_cert_to_PEM_cert(raw)]           # a binary (DER) certificate
        except ValueError:
            pems = []
    added = 0
    for pem in pems:
        info = _decode_cert(pem)
        if not info:
            continue
        os.makedirs(CA_DIR, mode=0o700, exist_ok=True)
        with open(os.path.join(CA_DIR, info["fingerprint"][:32] + ".pem"), "w") as f:
            f.write(pem.strip() + "\n")
        added += 1
    if not added:
        return False, "That file isn\u2019t a certificate (a private key or a .p12 / .pfx bundle can\u2019t be added here)."
    return ca_apply(), ""


def ca_remove(fingerprint):
    for c in ca_list():
        if c["fingerprint"] == fingerprint:
            try:
                os.remove(c["path"])
            except OSError:
                pass
    return ca_apply()


def ca_apply():
    """Hand the added certificates to the root helper (Chromium policy + system store)."""
    import json
    pems = []
    for c in ca_list():
        try:
            pems.append(open(c["path"]).read())
        except OSError:
            pass
    try:
        r = subprocess.run(["sudo", "-n", "/usr/local/sbin/litelink-web", "certs"], input=json.dumps(pems),
                           capture_output=True, text=True, timeout=60)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False
