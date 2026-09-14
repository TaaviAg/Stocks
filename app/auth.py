"""Password login for when the app is reachable from other devices.

The app holds a real trade log and has no accounts, so the moment it listens on
the home network (run-lan.bat) anything on that network could read and change
it. This module is the smallest thing that closes that: one password, a signed
session cookie, and a brake on guessing.

Three rules decide who must log in:

- **The laptop itself never does.** A request whose TCP peer is the loopback
  address can only come from this machine, so desktop use stays as it was.
  uvicorn is started with --no-proxy-headers so a network client cannot claim
  to be 127.0.0.1 through a forwarded header.
- **Everyone else does, once a password is set.** The session lasts 30 days, so
  a phone asks once a month, not once a visit.
- **With no password set, the network is refused outright** rather than let in.
  Starting the server on 0.0.0.0 without running set-password.bat first must
  fail closed, not open.

The password is stored only as a salted scrypt hash in data/auth.json, which is
gitignored with the rest of data/. Changing the password rotates the signing
key, which ends every existing session.

When the app moves to the cloud this is replaced by the host's own login; the
frontend only ever sees "401 -> go to /login", so nothing there changes.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time

from app.config import ROOT

AUTH_FILE = os.environ.get("STOCKS_AUTH_FILE") or os.path.join(ROOT, "data", "auth.json")
COOKIE = "stocks_session"
SESSION_SECONDS = 30 * 24 * 3600
LOOPBACK = {"127.0.0.1", "::1", "localhost"}

# scrypt cost: ~50 ms per check on a laptop. Slow enough to make guessing
# expensive, fast enough that a login does not feel sluggish.
_N, _R, _P = 2 ** 14, 8, 1

_lock = threading.Lock()
_failures = {}          # client ip -> [consecutive failures, locked until (epoch)]


def _hash(password, salt, n=_N, r=_R, p=_P):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p,
                          dklen=32, maxmem=64 * 1024 * 1024)


def _load():
    try:
        with open(AUTH_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def enabled():
    return _load() is not None


def set_password(password):
    if len(password) < 8:
        raise ValueError("Use at least 8 characters.")
    salt = secrets.token_bytes(16)
    record = {
        "salt": salt.hex(),
        "hash": _hash(password, salt).hex(),
        "n": _N, "r": _R, "p": _P,
        # A fresh signing key with every password: changing the password is
        # therefore also how to sign every device out.
        "secret": secrets.token_hex(32),
        "set_at": int(time.time()),
    }
    os.makedirs(os.path.dirname(AUTH_FILE), exist_ok=True)
    tmp = AUTH_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    os.replace(tmp, AUTH_FILE)


def check_password(password):
    rec = _load()
    if not rec:
        return False
    got = _hash(password, bytes.fromhex(rec["salt"]), rec["n"], rec["r"], rec["p"])
    return hmac.compare_digest(got, bytes.fromhex(rec["hash"]))


def is_loopback(host):
    return (host or "") in LOOPBACK


# ------------------------------------------------------------------ sessions

def _sign(secret, issued):
    return hmac.new(bytes.fromhex(secret), str(issued).encode(), hashlib.sha256).hexdigest()


def issue_token(now=None):
    rec = _load()
    if not rec:
        raise RuntimeError("No password set.")
    issued = int(now if now is not None else time.time())
    return "%d.%s" % (issued, _sign(rec["secret"], issued))


def valid_token(token, now=None):
    rec = _load()
    if not rec or not token or "." not in token:
        return False
    issued_s, sig = token.split(".", 1)
    try:
        issued = int(issued_s)
    except ValueError:
        return False
    now = now if now is not None else time.time()
    if issued > now + 60 or now - issued > SESSION_SECONDS:
        return False
    return hmac.compare_digest(sig, _sign(rec["secret"], issued))


# ------------------------------------------------------------------ guessing

def locked_for(ip, now=None):
    """Seconds this client must wait before another attempt, or 0."""
    now = now if now is not None else time.time()
    with _lock:
        entry = _failures.get(ip)
        return max(0, int(entry[1] - now + 0.999)) if entry else 0


def record_failure(ip, now=None):
    """Five free attempts, then a lockout that doubles each time, capped at an
    hour. Enough for typos; hopeless for guessing."""
    now = now if now is not None else time.time()
    with _lock:
        count, _until = _failures.get(ip, [0, 0])
        count += 1
        wait = 0 if count < 5 else min(3600, 30 * 2 ** (count - 5))
        _failures[ip] = [count, now + wait]
        return wait


def record_success(ip):
    with _lock:
        _failures.pop(ip, None)
