"""Free-text notes on orders: customer instructions and internal staff comments."""
from dataclasses import dataclass, field
from datetime import datetime

from shopkit.utils.dates import utc_now

MAX_NOTE_LENGTH = 500


@dataclass(frozen=True)
class Note:
    order_id: str
    author: str
    text: str
    internal: bool = True
    at: datetime = field(default_factory=utc_now)


class NoteBook:
    def __init__(self):
        self._notes: list[Note] = []

    def add(self, order_id: str, author: str, text: str, internal: bool = True) -> Note:
        text = " ".join(text.split())
        if not text:
            raise ValueError("empty note")
        if len(text) > MAX_NOTE_LENGTH:
            raise ValueError(f"note longer than {MAX_NOTE_LENGTH} characters")
        note = Note(order_id, author, text, internal)
        self._notes.append(note)
        return note

    def for_order(self, order_id: str, include_internal: bool = False) -> list[Note]:
        return [n for n in self._notes if n.order_id == order_id and (include_internal or not n.internal)]
