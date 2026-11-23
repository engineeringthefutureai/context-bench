"""SLA clocks in business hours (Mon-Fri 08:00-18:00 store time, holidays from utils.dates)."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from shopkit.support.tickets import ON_HOLD, PENDING_CUSTOMER, Ticket
from shopkit.utils.dates import is_business_day

OPEN_AT, CLOSE_AT = time(8, 0), time(18, 0)
FIRST_RESPONSE_MIN = {"urgent": 60, "high": 4 * 60, "normal": 8 * 60, "low": 16 * 60}
RESOLUTION_MIN = {"urgent": 8 * 60, "high": 2 * 600, "normal": 5 * 600, "low": 10 * 600}


def business_minutes(start: datetime, end: datetime) -> int:
    """Business minutes between two times (0 if end <= start)."""
    if end <= start:
        return 0
    total = 0
    day = start.date()
    while day <= end.date():
        if is_business_day(day):
            lo = max(start, datetime.combine(day, OPEN_AT))
            hi = min(end, datetime.combine(day, CLOSE_AT))
            if hi > lo:
                total += int((hi - lo).total_seconds() // 60)
        day += timedelta(days=1)
    return total


def add_business_minutes(start: datetime, minutes: int) -> datetime:
    """The time `minutes` business minutes after start."""
    current = start
    left = minutes
    while True:
        d = current.date()
        if is_business_day(d):
            open_dt, close_dt = datetime.combine(d, OPEN_AT), datetime.combine(d, CLOSE_AT)
            if current < open_dt:
                current = open_dt
            if current < close_dt:
                room = int((close_dt - current).total_seconds() // 60)
                if left <= room:
                    return current + timedelta(minutes=left)
                left -= room
        current = datetime.combine(d + timedelta(days=1), OPEN_AT)


def first_response_due(t: Ticket) -> datetime:
    return add_business_minutes(t.opened_at, FIRST_RESPONSE_MIN[t.priority])


def breached(t: Ticket, now: datetime) -> list[str]:
    """Which SLAs the ticket has missed. Time waiting for the customer or on hold doesn't count
    towards resolution (it is measured on agent-side time only, approximated by subtracting the
    business minutes since the last agent reply while pending)."""
    out = []
    responded = t.first_response_at or now
    if business_minutes(t.opened_at, responded) > FIRST_RESPONSE_MIN[t.priority]:
        out.append("first_response")
    end = t.solved_at or now
    spent = business_minutes(t.opened_at, end)
    if t.status in (PENDING_CUSTOMER, ON_HOLD) and t.messages:
        last_agent = next((m.at for m in reversed(t.messages) if m.author != "customer"), None)
        if last_agent:
            spent -= business_minutes(last_agent, end)
    if spent > RESOLUTION_MIN[t.priority]:
        out.append("resolution")
    return out
