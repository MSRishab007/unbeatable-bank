"""Account numbers with a Luhn check digit, and keyed hashing of PAN."""

import hashlib
import hmac
import os
import re

BRANCH_CODE = os.getenv("BRANCH_CODE", "5001")
PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def luhn_check_digit(digits: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:  # double every second digit, starting next to the check digit
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return str((10 - total % 10) % 10)


def is_valid_account_number(number: str) -> bool:
    return (
        len(number) == 12
        and number.isdigit()
        and luhn_check_digit(number[:-1]) == number[-1]
    )


def make_account_number(serial: int) -> str:
    """4-digit branch + 7-digit serial + check digit = 12 digits."""
    body = f"{BRANCH_CODE}{serial:07d}"
    return body + luhn_check_digit(body)


def hash_pan(pan: str) -> str:
    """HMAC, not plain SHA-256: PANs follow a small, guessable pattern, so an
    unkeyed hash could be reversed by brute force. The key lives in Secret Manager."""
    key = os.environ.get("PAN_HASH_KEY", "dev-only-pan-key").encode()
    return hmac.new(key, pan.upper().encode(), hashlib.sha256).hexdigest()
