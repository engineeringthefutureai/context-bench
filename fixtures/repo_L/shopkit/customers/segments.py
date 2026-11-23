"""Customer segments for targeted campaigns (RFM-style: recency, frequency, monetary)."""
from dataclasses import dataclass
from datetime import date

from shopkit.customers.models import Customer


@dataclass(frozen=True)
class Activity:
    customer_id: str
    last_order: date | None
    orders_12m: int
    spend_12m_cents: int


def segment(activity: Activity, today: date) -> str:
    """One of: new, champion, loyal, at_risk, lapsed. Thresholds agreed with marketing (2025)."""
    if activity.last_order is None:
        return "new"
    days = (today - activity.last_order).days
    if days > 365:
        return "lapsed"
    if days > 120:
        return "at_risk"
    if activity.orders_12m >= 6 and activity.spend_12m_cents >= 50_000:
        return "champion"
    return "loyal" if activity.orders_12m >= 3 else "new"


def members(customers: list[Customer], activity: dict[str, Activity], name: str,
            today: date) -> list[Customer]:
    return [c for c in customers if c.id in activity and segment(activity[c.id], today) == name]
