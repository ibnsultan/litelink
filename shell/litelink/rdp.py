"""Run FreeRDP 3 (xfreerdp3) for a connection and classify how it ended."""
import collections
import os
import signal
import subprocess
import threading

from gi.repository import GLib

XFREERDP = "xfreerdp3"

# xfreerdp client exit codes (FreeRDP 3, client/X11/xf_client.h).
ENDED = {1, 2, 3, 5, 6, 7, 9}            # server-side disconnect / logoff / timeouts
USER_CLOSED = {0, 11, 12, 145}           # user closed the session or cancelled
AUTH = {10, 132, 134, 135, 148, 149}     # bad credentials, locked out, password expired
UNREACHABLE = {131, 137, 139, 140, 141, 142, 147}
TLS = {143}


def build_args(conn, username=None, domain=None, ignore_cert=False):
    args = [XFREERDP, f"/v:{conn['host']}", "/log-level:ERROR"]
    user = username if username is not None else conn["username"]
    dom = domain if domain is not None else conn["domain"]
    if user:
        args.append(f"/u:{user}")
    if dom:
        args.append(f"/d:{dom}")
    if conn.get("gateway"):
        args.append(f"/g:{conn['gateway']}")

    disp = conn["display"]
    res = disp.get("resolution", "fullscreen")
    if res == "fullscreen":
        args.append("/f")
    else:
        args.append(f"/size:{res}")
    args.append(f"/bpp:{disp.get('depth', 32)}")
    if disp.get("multimon"):
        args.append("/multimon")
    if disp.get("dynamic"):
        args.append("/dynamic-resolution")

    dev = conn["devices"]
    args.append("+clipboard" if dev.get("clipboard") else "-clipboard")
    if dev.get("audio"):
        args.append("/sound")
    if dev.get("microphone"):
        args.append("/microphone")
    if dev.get("usb"):
        args.append("/drive:hotplug,*")
    if dev.get("smartcard"):
        args.append("/smartcard")

    args.append("/cert:ignore" if (ignore_cert or conn.get("ignore_cert")) else "/cert:tofu")
    # Credentials not on the command line are read from stdin, so the password
    # never shows up in the process list.
    args.append("/from-stdin:force")
    return args


def classify(code, stderr_text):
    low = stderr_text.lower()
    if code in USER_CLOSED:
        return "closed"
    if code in AUTH or "logon_failure" in low or "authentication" in low:
        return "auth"
    if code in TLS or "certificate" in low:
        return "certificate"
    if code in UNREACHABLE or "transport" in low or "connection refused" in low or "dns" in low:
        return "unreachable"
    if code in ENDED:
        return "ended"
    return "unreachable"


class Session:
    """One running xfreerdp3 process."""

    def __init__(self, conn, password, on_exit, username=None, domain=None, ignore_cert=False):
        self.conn = conn
        self.on_exit = on_exit
        self.stderr = collections.deque(maxlen=40)
        self.started = GLib.get_monotonic_time()
        self.cancelled = False
        args = build_args(conn, username, domain, ignore_cert)
        self.proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE, text=True, start_new_session=True)
        try:
            self.proc.stdin.write((password or "") + "\n")
            self.proc.stdin.close()
        except OSError:
            pass
        threading.Thread(target=self._drain, daemon=True).start()
        GLib.child_watch_add(GLib.PRIORITY_DEFAULT, self.proc.pid, self._exited)

    def _drain(self):
        for line in self.proc.stderr:
            self.stderr.append(line.rstrip())

    def _exited(self, pid, status):
        try:
            code = os.waitstatus_to_exitcode(status)
        except ValueError:
            code = 255
        text = "\n".join(self.stderr)
        seconds = (GLib.get_monotonic_time() - self.started) / 1e6
        kind = "closed" if self.cancelled else classify(code, text)
        GLib.idle_add(self.on_exit, kind, code, text, seconds, priority=GLib.PRIORITY_DEFAULT)

    def cancel(self):
        self.cancelled = True
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except OSError:
            pass
