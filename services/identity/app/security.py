"""Password and token primitives."""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import os
import secrets


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return f"scrypt${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$")
        candidate = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(candidate, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


# Used when the account doesn't exist, so a wrong email takes as long as a wrong password.
DUMMY_HASH = hash_password("not-a-real-password")


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def new_refresh_token(session_id: str) -> tuple[str, str]:
    """Returns (token to give the client, hash to store)."""
    secret = secrets.token_urlsafe(32)
    return f"{session_id}.{secret}", sha256(secret)


def hash_pin(pin: str, pepper: bytes) -> str:
    """A PIN has only 10^4 or 10^6 values, so a slow hash would not save it from someone who
    has the table: what protects it is the server-side pepper (not in the database) and the
    three-tries lock. HMAC keeps verification cheap enough to run on every payment."""
    salt = os.urandom(16)
    digest = hmac.new(pepper, salt + pin.encode(), hashlib.sha256).digest()
    return f"hmac${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_pin(pin: str, stored: str, pepper: bytes) -> bool:
    try:
        _, salt, digest = stored.split("$")
        candidate = hmac.new(pepper, base64.b64decode(salt) + pin.encode(), hashlib.sha256).digest()
        return hmac.compare_digest(candidate, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def new_otp() -> str:
    return f"{secrets.randbelow(10 ** 6):06d}"


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))
