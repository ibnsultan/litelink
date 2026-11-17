"""LiteLink configuration.

System defaults live in /etc/litelink/litelink.json (set by the image or an
administrator); the user's own copy in ~/.config/litelink/litelink.json
overrides them. Both are plain JSON so they can be provisioned centrally later.
"""
import copy
import hashlib
import json
import os
import uuid

SYSTEM_FILE = "/etc/litelink/litelink.json"
USER_FILE = os.path.expanduser("~/.config/litelink/litelink.json")

DEFAULT_CONNECTION = {
    "id": "",
    "name": "",
    "protocol": "rdp",
    "host": "",
    "username": "",
    "password": "",          # empty = ask when connecting
    "domain": "",
    "gateway": "",
    "vpn": "",               # WireGuard tunnel (NetworkManager uuid) brought up before connecting
    "auto": False,           # connect at startup
    "reconnect": True,       # reconnect when the session ends
    "ignore_cert": False,    # set when the user chose "remember this certificate"
    "display": {"resolution": "fullscreen", "depth": 32, "multimon": False, "dynamic": True},
    "devices": {"clipboard": True, "audio": True, "microphone": False, "usb": False, "smartcard": False},
}

DEFAULTS = {
    "connections": [],
    "lock_password_sha256": "",   # empty = lock screen unlocks without a password
    "clock_24h": True,
    "date_format": "short",       # see DATE_FORMATS
}

# Date formats offered in Settings > Time & region (strftime patterns)
DATE_FORMATS = [
    ("short", "%a %-d %b", "Sun 15 Nov"),
    ("dmy", "%d/%m/%Y", "15/11/2026"),
    ("mdy", "%m/%d/%Y", "11/15/2026"),
    ("iso", "%Y-%m-%d", "2026-11-15"),
]


def fmt_time(cfg_data, t):
    import time as _t
    return _t.strftime("%H:%M" if cfg_data.get("clock_24h", True) else "%-I:%M %p", t)


def fmt_date(cfg_data, t):
    import time as _t
    pattern = dict((k, p) for k, p, _e in DATE_FORMATS).get(cfg_data.get("date_format"), "%a %-d %b")
    return _t.strftime(pattern, t)


def _merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _read(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


class Config:
    def __init__(self):
        self.data = {}
        self.load()

    def load(self):
        data = _merge(DEFAULTS, _read(SYSTEM_FILE))
        data = _merge(data, _read(USER_FILE))
        data["connections"] = [normalize(c) for c in data.get("connections", [])]
        self.data = data

    def save(self):
        os.makedirs(os.path.dirname(USER_FILE), exist_ok=True)
        tmp = USER_FILE + ".tmp"
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, USER_FILE)

    # -- connections -------------------------------------------------------
    @property
    def connections(self):
        return self.data["connections"]

    def get(self, cid):
        return next((c for c in self.connections if c["id"] == cid), None)

    def upsert(self, conn):
        conn = normalize(conn)
        for i, c in enumerate(self.connections):
            if c["id"] == conn["id"]:
                self.connections[i] = conn
                break
        else:
            self.connections.append(conn)
        self.save()
        return conn

    def delete(self, cid):
        self.data["connections"] = [c for c in self.connections if c["id"] != cid]
        self.save()

    # -- lock --------------------------------------------------------------
    def lock_needs_password(self):
        return bool(self.data.get("lock_password_sha256"))

    def check_lock_password(self, pw):
        want = self.data.get("lock_password_sha256", "")
        return not want or hashlib.sha256(pw.encode()).hexdigest() == want


def normalize(conn):
    c = _merge(DEFAULT_CONNECTION, conn)
    if not c["id"]:
        c["id"] = uuid.uuid4().hex[:12]
    if not c["name"]:
        c["name"] = c["host"] or "New connection"
    return c
