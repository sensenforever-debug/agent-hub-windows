"""Per-source random validation epochs and private account-change fingerprints."""
import hashlib
import hmac
import secrets
import threading

import keyring

SERVICE = "agent-gateway-workbuddy-node"
SOURCES = ("workbuddy", "raccoon")
_lock = threading.RLock()


def _name(source, part):
    if source not in SOURCES:
        raise ValueError("unknown agent source")
    return f"credential-{part}-{source}"


def _new_epoch():
    return secrets.token_urlsafe(24)


def current(source, identity=None):
    """Return an opaque epoch; rotate only when a known account identity changes."""
    with _lock:
        name = _name(source, "epoch")
        epoch = keyring.get_password(SERVICE, name)
        if not epoch:
            epoch = _new_epoch()
            keyring.set_password(SERVICE, name, epoch)
        if isinstance(identity, str) and identity:
            secret = keyring.get_password(SERVICE, "credential-fingerprint-key")
            if not secret:
                secret = secrets.token_urlsafe(32)
                keyring.set_password(SERVICE, "credential-fingerprint-key", secret)
            fingerprint = hmac.new(secret.encode(), (source + "\0" + identity).encode(), hashlib.sha256).hexdigest()
            fingerprint_name = _name(source, "fingerprint")
            previous = keyring.get_password(SERVICE, fingerprint_name)
            if previous and not hmac.compare_digest(previous, fingerprint):
                epoch = _new_epoch()
                keyring.set_password(SERVICE, name, epoch)
            if previous != fingerprint:
                keyring.set_password(SERVICE, fingerprint_name, fingerprint)
        return epoch


def rotate(source):
    """Manual account-switch signal for sources without a readable identity."""
    with _lock:
        epoch = _new_epoch()
        keyring.set_password(SERVICE, _name(source, "epoch"), epoch)
        return epoch
