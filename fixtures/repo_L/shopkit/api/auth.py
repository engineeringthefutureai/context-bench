"""API key authentication and role checks for the admin API."""
import hashlib
import hmac
from dataclasses import dataclass

from shopkit.errors import ShopError


class Unauthorized(ShopError):
    http_status = 401


class Forbidden(ShopError):
    http_status = 403


ROLES = {"viewer": 0, "staff": 1, "admin": 2}


@dataclass(frozen=True)
class ApiKey:
    key_id: str
    secret_hash: str           # sha256 hex of the secret
    role: str


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class KeyRing:
    def __init__(self, keys: list[ApiKey]):
        self._keys = {k.key_id: k for k in keys}

    def authenticate(self, header: str | None) -> ApiKey:
        """Header format: "Key <key_id>:<secret>"."""
        if not header or not header.startswith("Key "):
            raise Unauthorized("missing API key")
        key_id, _, secret = header[4:].partition(":")
        key = self._keys.get(key_id)
        if key is None or not hmac.compare_digest(key.secret_hash, hash_secret(secret)):
            raise Unauthorized("invalid API key")
        return key


def require_role(key: ApiKey, role: str) -> None:
    if ROLES[key.role] < ROLES[role]:
        raise Forbidden(f"{role} role required", required=role, actual=key.role)
