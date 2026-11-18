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


def candidates(conn):
    """Addresses to try. People type "liteos.local", "192.168.1.10/os" or a full
    URL: without a scheme, https first, then http (LAN servers often have no TLS)."""
    host = (conn.get("host") or "").strip()
    if "://" in host:
        return [host]
    return ["https://" + host, "http://" + host]


def url_of(conn):
    return candidates(conn)[0]


def hostname(url):
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


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
    """Before opening: find an address that answers and whether it's LiteOS.
    -> dict(ok, url, liteos, detail). Any HTTP answer counts, even an error page
    or a certificate problem (Chromium shows those itself). Redirects are followed
    (a signed-out LiteOS answers with its sign-in page, which has the tag too)."""
    errors = []
    for url in candidates(conn):
        req = urllib.request.Request(url, headers={"User-Agent": "LiteLink"})
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ssl._create_unverified_context()) as r:
                head = r.read(256 * 1024).decode("utf-8", "replace")
            return {"ok": True, "url": url, "liteos": bool(LITEOS_META.search(head)), "detail": ""}
        except urllib.error.HTTPError as e:
            try:
                head = e.read(256 * 1024).decode("utf-8", "replace")
            except OSError:
                head = ""
            return {"ok": True, "url": url, "liteos": bool(LITEOS_META.search(head)), "detail": ""}
        except (urllib.error.URLError, OSError, ValueError) as e:
            errors.append(f"{url}: {getattr(e, 'reason', e)}")
    return {"ok": False, "url": url_of(conn), "liteos": False, "detail": "\n".join(errors)}


class Session:
    """One running Chromium window."""

    def __init__(self, conn, on_exit, url=None, liteos=False):
        """url / liteos: from probe() (the address that answered, and whether it
        identified itself as LiteOS)."""
        self.conn = conn
        self.on_exit = on_exit
        self.stderr = collections.deque(maxlen=40)
        self.started = GLib.get_monotonic_time()
        self.cancelled = False
        self.url = url or url_of(conn)
        self.liteos = liteos
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
        self.proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.PIPE, text=True, start_new_session=True)
        threading.Thread(target=self._drain, daemon=True).start()
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, self.proc.pid, self._exited)

    def _permissions(self):
        """Write (or clear) the LiteOS grants Chromium reads at start
        (litelink-web, a root helper: policy files are root-only)."""
        from . import system
        if self.liteos:
            parts = urllib.parse.urlsplit(self.url)
            origin = f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")
            system.run([WEB_HELPER, "grant", origin], sudo=True)
        else:
            system.run([WEB_HELPER, "clear"], sudo=True)

    # ---------------------------------------------------------------- extensions
    def _kiosk_extension(self):
        """LiteLink's own extension with this connection's settings."""
        dst = os.path.join(self.tmp, "litelink-kiosk")
        shutil.copytree(KIOSK_EXT, dst)
        web = self.conn.get("web", {})
        allowed = allowed_hosts(self.conn)
        if allowed:
            allowed = [hostname(self.url)] + allowed
        config = {"home": self.url, "allowed": allowed, "idleMinutes": int(web.get("idle_minutes") or 0)}
        with open(os.path.join(dst, "config.js"), "w") as f:
            f.write("self.LITELINK = " + json.dumps(config) + ";\n")
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
                         "const DEFAULT_HOSTS = " + json.dumps([hostname(self.url)]) + ";", src, count=1)
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
