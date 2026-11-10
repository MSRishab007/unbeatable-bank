"""JWT access tokens (HS256).

Identity & Auth issues them; the API Gateway verifies them and passes the
caller on to internal services in the X-Actor header:
    customer:<customer_id>            for customers
    staff:<staff_id>:<ROLE>           for bank staff
Internal services are not reachable from outside the cluster, so they trust X-Actor.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import UUID

import jwt

ALGORITHM = "HS256"
ISSUER = "unbeatable-bank"


class AuthError(Exception):
    pass


def issue_token(*, subject: str, role: str, secret: str, ttl_seconds: int, **claims) -> str:
    now = int(time.time())
    payload = {"iss": ISSUER, "sub": subject, "role": role, "iat": now, "exp": now + ttl_seconds, **claims}
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_token(token: str, secret: str) -> dict:
    try:
        return jwt.decode(token, secret, algorithms=[ALGORITHM], issuer=ISSUER,
                          options={"require": ["exp", "sub", "role"]})
    except jwt.PyJWTError as e:
        raise AuthError(str(e)) from e


@dataclass(frozen=True)
class Actor:
    kind: str            # customer | staff | system
    id: str
    role: str = ""       # staff only: ANALYST, ADMIN, KYC, TELLER

    @property
    def customer_id(self) -> UUID | None:
        return UUID(self.id) if self.kind == "customer" else None

    def header(self) -> str:
        return f"{self.kind}:{self.id}" + (f":{self.role}" if self.role else "")


def parse_actor(header: str | None) -> Actor:
    """Parse an X-Actor header. Unknown or missing -> system:unknown."""
    if not header:
        return Actor("system", "unknown")
    parts = header.split(":")
    if parts[0] == "customer" and len(parts) >= 2:
        try:
            UUID(parts[1])
        except ValueError:
            return Actor("system", "unknown")
        return Actor("customer", parts[1])
    if parts[0] == "staff" and len(parts) >= 2:
        return Actor("staff", parts[1], parts[2] if len(parts) > 2 else "")
    return Actor("system", ":".join(parts[1:]) or "unknown")
