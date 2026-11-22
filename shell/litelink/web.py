"""Web sessions (LiteOS and Web kiosk) on Chromium, run by LiteLink.

Chromium runs full screen in kiosk mode as its own process, like xfreerdp3 for RDP,
and reports back through the same on_exit(kind, code, text, seconds) interface.
What LiteLink controls:
  - a fresh profile per session (deleted afterwards), unless the connection keeps
    sign-ins, so nothing carries over between people;
  - system policies (/etc/chromium/policies/managed/litelink.json): no developer
    tools, incognito, sign-in, sync, password manager, printing or downloads to disk;
  - the LiteOS Frame Helper, set to this connection's LiteOS address, so the LiteOS
    Browser can show sites that refuse framing. Only when the address turns out to be
    a LiteOS instance: its pages carry <meta name="application-name" content="LiteOS">
    (checked by probe() before opening). The helper strips framing protection, so it
    must never be loaded for ordinary sites;
  - a small LiteLink extension, generated per session: allowed sites and the reset
    after a period of no use.
"""
import collections
import json
import os
import re
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request

from gi.repository import GLib

CHROMIUM = "chromium"
FRAME_HELPER = "/usr/lib/litelink/frame-helper"        # LiteOS Frame Helper (vendored in the image)
KIOSK_EXT = os.path.join(os.path.dirname(__file__), "webext")
WEB_HELPER = "/usr/local/sbin/litelink-web"            # LiteOS browser permissions (policy file)
PROFILES = os.path.expanduser("~/.config/litelink/web")  # kept profiles ("Keep sign-ins")

# A LiteOS instance identifies itself in its pages' <head>; either attribute order
LITEOS_META = re.compile(
    r'<meta\s+(?=[^>]*\bname\s*=\s*["\']application-name["\'])(?=[^>]*\bcontent\s*=\s*["\']LiteOS["\'])[^>]*>',
    re.I)


DEFAULT_PORTS = {"http": 80, "https": 443}


def split_address(address):
    """"https://liteos.local:8443/os" -> ("https://liteos.local/os", "8443"); the
    form keeps the port in a field of its own."""
    address = (address or "").strip()
    probe_url = address if "://" in address else "https://" + address
    try:
        parts = urllib.parse.urlsplit(probe_url)
        port = parts.port
    except ValueError:
        return address, ""
    if not port or not parts.hostname:
        return address, ""
    host = parts.hostname if ":" not in parts.hostname else f"[{parts.hostname}]"
    netloc = (parts.netloc.rsplit("@", 1)[0] + "@" if "@" in parts.netloc else "") + host
    rest = urllib.parse.urlunsplit(("", "", parts.path, parts.query, parts.fragment))
    return (f"{parts.scheme}://" if "://" in address else "") + netloc + rest, str(port)


def with_port(url, port):
    """url with port set (left out when it's the scheme's default)."""
    port = str(port or "").strip()
    if not port.isdigit():
        return url
    parts = urllib.parse.urlsplit(url)
    if not parts.hostname:
        return url
    host = parts.hostname if ":" not in parts.hostname else f"[{parts.hostname}]"
    netloc = host if int(port) == DEFAULT_PORTS.get(parts.scheme) else f"{host}:{port}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def candidates(conn):
    """Addresses to try. People type "liteos.local", "192.168.1.10/os" or a full
    URL: without a scheme, https first, then http (LAN servers often have no TLS).
    The port field (web.port) applies to each; empty = the scheme's own (443 / 80)."""
    host = (conn.get("host") or "").strip()
    port = str(conn.get("web", {}).get("port") or "").strip()
    urls = [host] if "://" in host else ["https://" + host, "http://" + host]
    return [with_port(u, port) for u in urls]


def url_of(conn):
    return candidates(conn)[0]


def hostname(url):
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def origin_of(url):
    """scheme://host[:port] as browsers write an origin (default ports left out)."""
    parts = urllib.parse.urlsplit(url)
    port = parts.port
    default = {"http": 80, "https": 443}.get(parts.scheme)
    return f"{parts.scheme}://{(parts.hostname or '').lower()}" + (f":{port}" if port and port != default else "")


def allowed_hosts(conn):
    """Sites a Web kiosk may open, besides its own address (empty = any)."""
    raw = conn.get("web", {}).get("allowed", "")
    out = []
    for part in raw.replace(",", " ").split():
        h = hostname(part if "://" in part else "https://" + part)
        if h and h not in out:
            out.append(h)
    return out


def probe(conn, timeout=8):
    """Before opening (and in the connection form): find an address that answers
    and whether it's LiteOS. -> dict(ok, url, final, liteos, detail). url is the
    address that answered (Chromium opens it); final is where its redirects ended
    (http -> https, a move to www.), which is LiteOS's real origin. Any HTTP answer
    counts, even an error page or a certificate problem (Chromium shows those
    itself). A signed-out LiteOS redirects to its sign-in page, which has the tag too."""
    errors = []
    for url in candidates(conn):
        req = urllib.request.Request(url, headers={"User-Agent": "LiteLink"})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl._create_unverified_context()) as r:
                head = r.read(256 * 1024).decode("utf-8", "replace")
                final = r.geturl() or url
            return {"ok": True, "url": url, "final": final, "liteos": bool(LITEOS_META.search(head)), "detail": ""}
        except urllib.error.HTTPError as e:
            try:
                head = e.read(256 * 1024).decode("utf-8", "replace")
            except OSError:
                head = ""
            return {"ok": True, "url": url, "final": e.geturl() or url, "liteos": bool(LITEOS_META.search(head)),
                    "detail": ""}
        except (urllib.error.URLError, OSError, ValueError) as e:
            errors.append(f"{url}: {getattr(e, 'reason', e)}")
    return {"ok": False, "url": url_of(conn), "final": url_of(conn), "liteos": False, "detail": "\n".join(errors)}


class Session:
    """One running Chromium window."""

    def __init__(self, conn, on_exit, url=None, liteos=False, final=None, app=None):
        """url / final / liteos: from probe() (the address that answered, where its
        redirects ended, and whether the page identified itself as LiteOS).
        The LiteOS extras (window.LiteLink, the Frame Helper, the browser
        permission grants) need both: the admin ticked "This is LiteOS" on the
        connection (web.liteos) and the page still says it is LiteOS. A page
        alone never gets them."""
        self.conn = conn
        self.on_exit = on_exit
        self.stderr = collections.deque(maxlen=40)
        self.started = GLib.get_monotonic_time()
        self.cancelled = False
        self.url = url or url_of(conn)
        self.liteos = bool(liteos) and conn.get("web", {}).get("liteos") is True
        # LiteOS's real origin: where the address's redirects ended up
        self.origin = origin_of(final or self.url)
        self.home_host = hostname(final or self.url)
        keep = conn.get("web", {}).get("keep_signins")
        self.tmp = tempfile.mkdtemp(prefix="litelink-web-", dir=os.environ.get("XDG_RUNTIME_DIR") or None)
        if keep:
            profile = os.path.join(PROFILES, conn["id"])
            os.makedirs(profile, mode=0o700, exist_ok=True)
        else:
            profile = os.path.join(self.tmp, "profile")
        # Browser permissions: LiteOS gets the clipboard, notifications, camera and
        # the rest without prompts (kiosk mode can't show them); other sites nothing
        self._permissions()
        self._printing()
        exts = [self._kiosk_extension()]
        if self.liteos:
            helper = self._frame_helper()
            if helper:
                exts.append(helper)
        args = [CHROMIUM, "--kiosk", f"--user-data-dir={profile}",
                "--no-first-run", "--no-default-browser-check", "--noerrdialogs",
                "--disable-session-crashed-bubble", "--disable-infobars", "--password-store=basic",
                "--check-for-update-interval=31536000", "--overscroll-history-navigation=0",
                # Recent Chromium ignores --load-extension unless this feature is turned off
                "--disable-features=DisableLoadExtensionCommandLineSwitch,Translate,MediaRouter",
                "--load-extension=" + ",".join(exts), self.url]
        env = dict(os.environ)
        self.requests = None
        if self.liteos:
            # LiteLink's own screens for LiteOS (admin password, lock, Settings): the
            # bridge asks over a socket in this session's private folder (bridge.py)
            from .bridge import Requests
            self.socket_path = os.path.join(self.tmp, "shell.sock")
            if app is not None:
                self.requests = Requests(app, self.socket_path)
            # The device bridge (litelink-devices, started by Chromium for LiteLink's
            # kiosk extension) reads this session's LiteOS origin and what it may use from here
            env["LITELINK_SESSION"] = self._bridge_session()
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE, text=True, start_new_session=True, env=env)
        threading.Thread(target=self._drain, daemon=True).start()
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, self.proc.pid, self._exited)

    def _permissions(self):
        """Write (or clear) the LiteOS grants Chromium reads at start
        (litelink-web, a root helper: policy files are root-only)."""
        from . import system
        if self.liteos:
            system.run([WEB_HELPER, "grant", self.origin], sudo=True)
        else:
            system.run([WEB_HELPER, "clear"], sudo=True)

    def printers_allowed(self):
        """Printing for this session: a LiteOS connection with Printers on, and the print module installed."""
        from . import system
        return bool(self.liteos and self.conn.get("web", {}).get("printers", True) is not False
                    and system.print_engine())

    def _printing(self):
        """Chromium's own printing (window.print()) follows the same setting as the device
        bridge's (litelink-web: PrintingEnabled is a root-only policy, rewritten every session)."""
        from . import system
        system.run([WEB_HELPER, "print", "on" if self.printers_allowed() else "off"], sudo=True)

    def _bridge_session(self):
        """session.json for litelink-devices: the LiteOS origin it may send files
        to, the USB drive setting (off / read / write; read unless the connection says)
        and whether it may print."""
        usb = self.conn.get("web", {}).get("usb", "read")
        path = os.path.join(self.tmp, "session.json")
        with open(path, "w") as f:
            json.dump({"liteos": self.origin, "usb": usb if usb in ("off", "read", "write") else "read",
                       # the connection's setting; the bridge checks the print module itself
                       # (so a page hears "isn't installed" rather than "turned off")
                       "printers": bool(self.liteos and self.conn.get("web", {}).get("printers", True) is not False),
                       "shell": self.socket_path if self.requests is not None else ""}, f)
        return path

    # ---------------------------------------------------------------- extensions
    def _kiosk_extension(self):
        """LiteLink's own extension with this connection's settings."""
        dst = os.path.join(self.tmp, "litelink-kiosk")
        shutil.copytree(KIOSK_EXT, dst)
        web = self.conn.get("web", {})
        allowed = allowed_hosts(self.conn)
        if allowed:
            allowed = list(dict.fromkeys([hostname(self.url), self.home_host] + allowed))
        liteos = self.origin if self.liteos else ""
        config = {"home": self.url, "allowed": allowed, "idleMinutes": int(web.get("idle_minutes") or 0),
                  "liteos": liteos}
        with open(os.path.join(dst, "config.js"), "w") as f:
            f.write("self.LITELINK = " + json.dumps(config) + ";\n")
        if liteos:
            # window.LiteLink in every frame of the LiteOS address (docs/litelink-web-api.md):
            # the API in the page's world, its relay in the extension's
            # (match patterns can't name a port: relay.js checks the exact origin)
            parts = urllib.parse.urlsplit(self.origin)
            match = [f"{parts.scheme}://{parts.hostname}/*"]
            manifest = os.path.join(dst, "manifest.json")
            with open(manifest) as f:
                m = json.load(f)
            m["content_scripts"] = [
                {"matches": match, "all_frames": True, "run_at": "document_start",
                 "js": ["config.js", "relay.js"]},
                {"matches": match, "all_frames": True, "run_at": "document_start", "world": "MAIN",
                 "js": ["litelink-api.js"]},
            ]
            with open(manifest, "w") as f:
                json.dump(m, f, indent=2)
        return dst

    def _frame_helper(self):
        """A copy of the LiteOS Frame Helper set to this connection's LiteOS host."""
        if not os.path.isdir(FRAME_HELPER):
            return None
        dst = os.path.join(self.tmp, "frame-helper")
        shutil.copytree(FRAME_HELPER, dst)
        bg = os.path.join(dst, "background.js")
        with open(bg) as f:
            src = f.read()
        # The helper's host list (whatever it holds) becomes this connection's LiteOS host
        src, n = re.subn(r"const DEFAULT_HOSTS = \[[^\]]*\];",
                         "const DEFAULT_HOSTS = " + json.dumps([self.home_host]) + ";", src, count=1)
        if not n:
            print("web: frame helper has no DEFAULT_HOSTS line; LiteOS host not set", file=sys.stderr)
        with open(bg, "w") as f:
            f.write(src)
        return dst

    # ---------------------------------------------------------------- process
    def _drain(self):
        for line in self.proc.stderr:
            self.stderr.append(line.rstrip())

    def _exited(self, pid, status):
        try:
            code = os.waitstatus_to_exitcode(status)
        except ValueError:
            code = 255
        seconds = (GLib.get_monotonic_time() - self.started) / 1e6
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self.liteos:
            # The device bridge unmounts the USB drives it used when Chromium goes;
            # this catches the cases where it couldn't (killed with the session)
            from . import system
            threading.Thread(target=system.media_unmount, daemon=True).start()
            if self.printers_allowed():
                # the default printer is the one used last, Chromium's own print dialog included
                threading.Thread(target=system.printer_last, daemon=True).start()
        # Closed by the user (End session) or Chromium quit normally: back to the
        # desktop / kiosk. Anything else is a crash.
        kind = "closed" if self.cancelled or code == 0 else "ended"
        text = "\n".join(self.stderr)
        GLib.idle_add(self.on_exit, kind, code, text, seconds, priority=GLib.PRIORITY_DEFAULT)

    def cancel(self):
        self.cancelled = True
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except OSError:
            pass
