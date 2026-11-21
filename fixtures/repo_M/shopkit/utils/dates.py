"""Calendar helpers shared by shipping estimates, reports and payment terms."""
from datetime import date, datetime, timedelta, timezone

# Fixed public holidays observed by the warehouse (month, day). Movable feasts are not modelled.
HOLIDAYS = {(1, 1), (7, 4), (12, 25), (12, 26)}


def is_business_day(day: date) -> bool:
    return day.weekday() < 5 and (day.month, day.day) not in HOLIDAYS


def add_business_days(start: date, days: int) -> date:
    """The date `days` business days after `start` (weekends and HOLIDAYS skipped).
    add_business_days(friday, 1) is the following Monday. days=0 returns `start` unchanged."""
    if days < 0:
        raise ValueError("days must be >= 0")
    current = start
    while days:
        current += timedelta(days=1)
        if is_business_day(current):
            days -= 1
    return current


def iso_week_label(day: date) -> str:
    """Week bucket used in dashboards, e.g. date(2026, 11, 18) -> "2026-W47"."""
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_iso_date(text: str) -> date:
    """YYYY-MM-DD only; anything else raises ValueError."""
    return date.fromisoformat(text.strip())


def month_bounds(day: date) -> tuple[date, date]:
    """First and last day of the month containing `day`."""
    first = day.replace(day=1)
    next_month = (first + timedelta(days=32)).replace(day=1)
    return first, next_month - timedelta(days=1)
