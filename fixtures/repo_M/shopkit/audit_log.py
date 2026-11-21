"""Append-only audit trail of who changed what. Kept in memory; the API exposes it to admins."""
from dataclasses import dataclass, field
from datetime import datetime

from shopkit.events import Event
from shopkit.utils.dates import utc_now


@dataclass(frozen=True)
class AuditEntry:
    actor: str
    action: str
    target: str
    details: dict = field(default_factory=dict)
    at: datetime = field(default_factory=utc_now)


class AuditLog:
    def __init__(self):
        self._entries: list[AuditEntry] = []

    def record(self, actor: str, action: str, target: str, **details) -> AuditEntry:
        entry = AuditEntry(actor, action, target, details)
        self._entries.append(entry)
        return entry

    def for_target(self, target: str) -> list[AuditEntry]:
        return [e for e in self._entries if e.target == target]

    def on_event(self, event: Event) -> None:
        """Bus subscriber: record every domain event under the "system" actor."""
        target = str(event.payload.get("order_id") or event.payload.get("sku") or "-")
        self.record("system", event.name, target, **event.payload)

    def __len__(self) -> int:
        return len(self._entries)
