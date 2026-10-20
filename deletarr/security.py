import hashlib
import hmac
import ipaddress
import os
import secrets
import socket
from pathlib import Path
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet

from .db import now

PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


class SafetyError(ValueError):
    """An operation was refused without revealing external response bodies or credentials."""


class Vault:
    def __init__(self, directory):
        path = Path(directory) / "credentials.key"
        if not path.exists():
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as file:
                file.write(Fernet.generate_key())
        if path.stat().st_mode & 0o077:
            raise SafetyError("Credential key permissions must be 0600")
        self.cipher = Fernet(path.read_bytes())

    def encrypt(self, value):
        return self.cipher.encrypt(value.encode()).decode()

    def decrypt(self, value):
        return self.cipher.decrypt(value.encode()).decode()


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def authenticate(db, username, password, key):
    with db.transaction() as connection:
        rate = connection.execute("SELECT * FROM login_attempts WHERE key=?", (key,)).fetchone()
        if rate and rate["until_at"] > now() and rate["count"] >= 5:
            raise SafetyError("Too many sign-in attempts. Try again in 15 minutes.")
        count = rate["count"] + 1 if rate and rate["until_at"] > now() else 1
        connection.execute("INSERT OR REPLACE INTO login_attempts VALUES(?,?,?)", (key, count, now() + 900))
    row = db.one("SELECT * FROM administrator WHERE id=1")
    # Hash even on unknown username to avoid a cheap username oracle.
    valid = False
    try:
        valid = PASSWORDS.verify(row["password_hash"] if row else PASSWORDS.hash(secrets.token_hex()), password)
    except VerificationError:
        pass
    if not row or not hmac.compare_digest(row["username"].encode(), username.encode()) or not valid:
        raise SafetyError("Invalid username or password")
    db.execute("DELETE FROM login_attempts WHERE key=?", (key,))
    return new_session(db)


def new_session(db):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    db.execute("INSERT INTO sessions VALUES(?,?,?)", (token_hash(token), csrf, now() + 43200))
    return token, csrf


def url_parts(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise SafetyError("Use an HTTP(S) URL without embedded credentials")
    if parsed.query or parsed.fragment or "\\" in url or any(ord(ch) < 32 for ch in url):
        raise SafetyError("URL must not contain a query, fragment or control characters")
    return parsed


def resolve_url(url, allow_private=False):
    parsed = url_parts(url)
    try:
        addresses = socket.getaddrinfo(
            parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM
        )
    except (socket.gaierror, ValueError) as exc:
        raise SafetyError("Destination hostname could not be resolved") from exc
    resolved = []
    for entry in addresses:
        address = ipaddress.ip_address(entry[4][0].split("%")[0])
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        if str(address) in {"fd00:ec2::254", "100.100.100.200", "168.63.129.16"}:
            raise SafetyError("Cloud instance metadata destinations are prohibited")
        if address.is_link_local or address.is_multicast or address.is_unspecified:
            raise SafetyError("Link-local, multicast and unspecified destinations are prohibited")
        if not address.is_global and not allow_private:
            raise SafetyError("Private/LAN destinations require explicit trust")
        resolved.append(str(address))
    if not resolved:
        raise SafetyError("Destination has no usable addresses")
    return url.rstrip("/"), resolved


def validate_url(url, allow_private=False, resolve=True):
    if not resolve:
        url_parts(url)
        return url.rstrip("/")
    return resolve_url(url, allow_private)[0]
