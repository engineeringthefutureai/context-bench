"""Identifiers. Internal ids are prefixed random hex; customer-facing codes avoid look-alike characters."""
import secrets

# No 0/O, 1/I/L: these codes get read out over the phone and typed from printed slips.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def new_id(prefix: str) -> str:
    """e.g. new_id("ord") -> "ord_3f9a1c2b7d4e"."""
    return f"{prefix}_{secrets.token_hex(6)}"


def short_code(length: int = 8, groups: int = 0) -> str:
    """Random human-friendly code from an unambiguous alphabet, e.g. "K7QM-X2PA" with groups=4.
    groups=0 means no dashes."""
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(length))
    if groups:
        code = "-".join(code[i:i + groups] for i in range(0, len(code), groups))
    return code


def is_short_code(text: str) -> bool:
    return bool(text) and all(c in _CODE_ALPHABET or c == "-" for c in text)
