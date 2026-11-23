"""Audiences for campaign sends: who gets a campaign, built from segments, consent and send history.

Rules (marketing + legal, 2025): only opted-in customers; nobody suppressed (bounces, complaints,
unsubscribes, see notifications.email.SuppressionList); at most MAX_SENDS_PER_WEEK marketing emails
per customer in any 7 days; a holdout share is kept out of every send to measure the campaign's effect."""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from shopkit.customers.models import Customer
from shopkit.customers.segments import Activity, segment
from shopkit.marketing.campaigns import Campaign

MAX_SENDS_PER_WEEK = 2
DEFAULT_HOLDOUT_PCT = 5


@dataclass
class SendLog:
    sends: dict[str, list[date]] = field(default_factory=lambda: defaultdict(list))     # customer -> send dates

    def record(self, customer_id: str, day: date) -> None:
        self.sends[customer_id].append(day)

    def count_last_week(self, customer_id: str, day: date) -> int:
        start = day - timedelta(days=6)
        return sum(1 for d in self.sends.get(customer_id, []) if start <= d <= day)


def in_holdout(customer_id: str, campaign_handle: str, pct: int = DEFAULT_HOLDOUT_PCT) -> bool:
    """Stable per customer and campaign: the same customer is in or out every time it's computed."""
    h = int(hashlib.sha256(f"{campaign_handle}:{customer_id}".encode()).hexdigest()[:8], 16)
    return h % 100 < pct


@dataclass
class Audience:
    campaign: str
    recipients: list[Customer] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)        # customer id -> reason

    @property
    def size(self) -> int:
        return len(self.recipients)


def build_audience(campaign: Campaign, customers: list[Customer], activity: dict[str, Activity], today: date, *,
                   segments: set[str] | None = None, suppressed: set[str] | None = None,
                   send_log: SendLog | None = None, holdout_pct: int = DEFAULT_HOLDOUT_PCT,
                   tags_any: set[str] | None = None) -> Audience:
    """Recipients for a campaign send today. `suppressed` holds lower-case email addresses."""
    suppressed = suppressed or set()
    send_log = send_log or SendLog()
    audience = Audience(campaign.handle)
    if not campaign.running(today):
        for c in customers:
            audience.excluded[c.id] = "campaign not running"
        return audience
    for c in sorted(customers, key=lambda c: c.id):
        if not c.marketing_opt_in:
            audience.excluded[c.id] = "no consent"
            continue
        if c.email.strip().lower() in suppressed:
            audience.excluded[c.id] = "suppressed"
            continue
        if segments:
            act = activity.get(c.id)
            seg = segment(act, today) if act else "new"
            if seg not in segments:
                audience.excluded[c.id] = f"segment {seg}"
                continue
        if tags_any and not (c.tags & tags_any):
            audience.excluded[c.id] = "tags"
            continue
        if send_log.count_last_week(c.id, today) >= MAX_SENDS_PER_WEEK:
            audience.excluded[c.id] = "frequency cap"
            continue
        if in_holdout(c.id, campaign.handle, holdout_pct):
            audience.excluded[c.id] = "holdout"
            continue
        audience.recipients.append(c)
    return audience


def lift(converted_sent: int, sent: int, converted_holdout: int, holdout: int) -> float | None:
    """Relative conversion lift of the send over the holdout; None without data."""
    if not sent or not holdout or not converted_holdout:
        return None
    return round((converted_sent / sent) / (converted_holdout / holdout) - 1, 4)
