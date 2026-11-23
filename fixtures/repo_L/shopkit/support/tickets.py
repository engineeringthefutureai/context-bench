from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from shopkit.errors import Conflict, NotFound, ShopError
from shopkit.events import BUS, EventBus
from shopkit.utils.ids import new_id

OPEN, PENDING_CUSTOMER, ON_HOLD, SOLVED, CLOSED = "open", "pending_customer", "on_hold", "solved", "closed"
PRIORITIES = ("low", "normal", "high", "urgent")
REOPEN_WINDOW_DAYS = 7          # a reply to a solved ticket within this reopens it; later, a new ticket


class TicketNotFound(NotFound):
    pass


class TicketClosed(Conflict):
    pass


@dataclass
class Message:
    author: str                  # "customer" or an agent id
    body: str
    at: datetime
    internal: bool = False


@dataclass
class Ticket:
    id: str
    subject: str
    customer_id: str | None
    opened_at: datetime
    order_id: str | None = None
    status: str = OPEN
    priority: str = "normal"
    queue: str = "general"
    assignee: str | None = None
    tags: set[str] = field(default_factory=set)
    messages: list[Message] = field(default_factory=list)
    first_response_at: datetime | None = None
    solved_at: datetime | None = None

    @property
    def last_public(self) -> Message | None:
        return next((m for m in reversed(self.messages) if not m.internal), None)


ORDER_REF = re.compile(r"\b(ord_[0-9a-f]{12}|SOB?-\d{4}-\d{1,6})\b", re.I)


class Desk:
    def __init__(self, bus: EventBus = BUS):
        self.tickets: dict[str, Ticket] = {}
        self.bus = bus

    def open(self, subject: str, body: str, at: datetime, *, customer_id: str | None = None,
             order_id: str | None = None, channel: str = "email") -> Ticket:
        subject = " ".join(subject.split())[:200] or "(no subject)"
        if order_id is None:
            m = ORDER_REF.search(subject + " " + body)
            order_id = m.group(1) if m else None
        ticket = Ticket(new_id("tkt"), subject, customer_id, at, order_id=order_id)
        ticket.messages.append(Message("customer", body, at))
        ticket.tags.add(f"channel:{channel}")
        self.tickets[ticket.id] = ticket
        self.bus.publish("ticket.opened", ticket_id=ticket.id, order_id=order_id, channel=channel)
        return ticket

    def get(self, ticket_id: str) -> Ticket:
        try:
            return self.tickets[ticket_id]
        except KeyError:
            raise TicketNotFound(f"no ticket {ticket_id}", ticket_id=ticket_id) from None

    def reply(self, ticket_id: str, author: str, body: str, at: datetime, *, internal: bool = False,
              solve: bool = False) -> Ticket:
        t = self.get(ticket_id)
        if t.status == CLOSED:
            raise TicketClosed(f"ticket {ticket_id} is closed", ticket_id=ticket_id)
        if not body.strip():
            raise ShopError("empty reply")
        t.messages.append(Message(author, body, at, internal))
        if author == "customer":
            if t.status in (PENDING_CUSTOMER, SOLVED):
                t.status, t.solved_at = OPEN, None
        elif not internal:
            if t.first_response_at is None:
                t.first_response_at = at
            t.status = SOLVED if solve else PENDING_CUSTOMER
            if solve:
                t.solved_at = at
        return t

    def customer_email(self, ticket_id: str | None, subject: str, body: str, at: datetime,
                       customer_id: str | None = None) -> Ticket:
        """Inbound email: threads into the ticket it replies to unless that ticket is closed or was
        solved more than REOPEN_WINDOW_DAYS ago, in which case a new ticket is opened."""
        if ticket_id and ticket_id in self.tickets:
            t = self.tickets[ticket_id]
            stale = t.status == CLOSED or (t.status == SOLVED and t.solved_at and (at - t.solved_at).days > REOPEN_WINDOW_DAYS)
            if not stale:
                return self.reply(ticket_id, "customer", body, at)
            new = self.open(subject, body, at, customer_id=customer_id or t.customer_id, order_id=t.order_id)
            new.tags.add(f"follow_up:{t.id}")
            return new
        return self.open(subject, body, at, customer_id=customer_id)

    def close_solved(self, now: datetime, after_days: int = 4) -> list[str]:
        done = []
        for t in self.tickets.values():
            if t.status == SOLVED and t.solved_at and (now - t.solved_at).days >= after_days:
                t.status = CLOSED
                done.append(t.id)
        return done
