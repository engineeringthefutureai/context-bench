"""Input validation for customer-facing forms and imports."""
import re

_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}$")
_PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def is_valid_email(address: str) -> bool:
    """Pragmatic check used at signup and checkout: one @, a dotted domain, no spaces.
    Does not try to implement RFC 5322."""
    address = address.strip()
    return len(address) <= 254 and bool(_EMAIL_RE.match(address)) and ".." not in address


def normalize_phone(raw: str) -> str:
    """E.164-ish: keep digits and a leading +. Raises ValueError if the result doesn't look valid."""
    digits = re.sub(r"[^\d+]", "", raw.strip())
    if digits.startswith("00"):
        digits = "+" + digits[2:]
    if not _PHONE_RE.match(digits):
        raise ValueError(f"invalid phone number: {raw!r}")
    return digits


def require(condition: bool, message: str) -> None:
    """Raise ValueError(message) unless condition holds. Used by importers and form handlers."""
    if not condition:
        raise ValueError(message)


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))
