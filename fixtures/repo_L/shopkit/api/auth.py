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


# ---- scopes, sessions and key rotation (2025) --------------------------------------------------
# Partner integrations get scoped keys instead of a role. A key with scopes is limited to them even
# if its role would allow more; keys without scopes behave as before (role only).
SCOPES = {"orders:read", "orders:write", "catalog:read", "catalog:write", "customers:read",
          "refunds:write", "webhooks:manage", "reports:read"}
SESSION_TTL_S = 8 * 3600
ROTATION_GRACE_S = 24 * 3600            # an old secret keeps working this long after rotation


@dataclass(frozen=True)
class ScopedKey:
    key: ApiKey
    scopes: frozenset[str] = frozenset()
    previous_secret_hash: str | None = None
    rotated_at: float | None = None


def require_scope(scoped: ScopedKey, scope: str) -> None:
    if scope not in SCOPES:
        raise ValueError(f"unknown scope {scope!r}")
    if scoped.scopes and scope not in scoped.scopes:
        raise Forbidden(f"scope {scope} required", required=scope, scopes=sorted(scoped.scopes))


class ScopedKeyRing:
    """KeyRing with scopes and rotation. `authenticate` accepts the previous secret during the grace
    period so partners can deploy the new one without downtime."""

    def __init__(self, keys: list[ScopedKey], clock=None):
        import time
        self._keys = {k.key.key_id: k for k in keys}
        self._clock = clock or time.time

    def authenticate(self, header: str | None) -> ScopedKey:
        if not header or not header.startswith("Key "):
            raise Unauthorized("missing API key")
        key_id, _, secret = header[4:].partition(":")
        scoped = self._keys.get(key_id)
        if scoped is None:
            raise Unauthorized("invalid API key")
        given = hash_secret(secret)
        if hmac.compare_digest(scoped.key.secret_hash, given):
            return scoped
        if (scoped.previous_secret_hash and scoped.rotated_at is not None
                and self._clock() - scoped.rotated_at <= ROTATION_GRACE_S
                and hmac.compare_digest(scoped.previous_secret_hash, given)):
            return scoped
        raise Unauthorized("invalid API key")

    def rotate(self, key_id: str, new_secret: str) -> ScopedKey:
        old = self._keys[key_id]
        rotated = ScopedKey(ApiKey(key_id, hash_secret(new_secret), old.key.role), old.scopes,
                            previous_secret_hash=old.key.secret_hash, rotated_at=self._clock())
        self._keys[key_id] = rotated
        return rotated


@dataclass
class Session:
    token: str
    user: str
    role: str
    expires_at: float


class SessionStore:
    """Admin UI sessions (cookie token -> session). In memory; the admin runs as one process."""

    def __init__(self, clock=None):
        import time
        self._sessions: dict[str, Session] = {}
        self._clock = clock or time.time

    def create(self, user: str, role: str) -> Session:
        import secrets
        if role not in ROLES:
            raise ValueError(f"unknown role {role!r}")
        session = Session(secrets.token_urlsafe(32), user, role, self._clock() + SESSION_TTL_S)
        self._sessions[session.token] = session
        return session

    def get(self, token: str | None) -> Session:
        session = self._sessions.get(token or "")
        if session is None or session.expires_at < self._clock():
            self._sessions.pop(token or "", None)
            raise Unauthorized("session expired")
        return session

    def end(self, token: str) -> None:
        self._sessions.pop(token, None)

    def purge(self) -> int:
        now = self._clock()
        dead = [t for t, s in self._sessions.items() if s.expires_at < now]
        for t in dead:
            del self._sessions[t]
        return len(dead)
