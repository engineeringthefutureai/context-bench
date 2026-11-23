"""Turn signals into a decision, and keep the review queue."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from shopkit.errors import Conflict, NotFound
from shopkit.events import BUS, EventBus
from shopkit.fraud.rules import OrderFacts, Signal, evaluate

ACCEPT, REVIEW, REJECT = "accept", "review", "reject"
REVIEW_AT = 40
REJECT_AT = 90
REVIEW_SLA = timedelta(hours=4)            # orders not reviewed in time are accepted (ops decision, see below)


@dataclass
class Assessment:
    order_id: str
    score: int
    decision: str
    signals: list[Signal]
    at: datetime


def assess(facts: OrderFacts, at: datetime, *, review_at: int = REVIEW_AT, reject_at: int = REJECT_AT) -> Assessment:
    signals = evaluate(facts)
    score = max(sum(s.points for s in signals), 0)
    decision = REJECT if score >= reject_at else REVIEW if score >= review_at else ACCEPT
    # a chargeback history always goes to a person, even when other signals cancel it out
    if decision == ACCEPT and any(s.code == "chargeback_history" for s in signals):
        decision = REVIEW
    return Assessment(facts.order_id, score, decision, signals, at)


class ReviewNotFound(NotFound):
    pass


@dataclass
class ReviewItem:
    assessment: Assessment
    queued_at: datetime
    assignee: str | None = None
    outcome: str | None = None              # ACCEPT / REJECT once decided
    decided_at: datetime | None = None
    note: str = ""


@dataclass
class ReviewQueue:
    items: dict[str, ReviewItem] = field(default_factory=dict)
    bus: EventBus = BUS

    def add(self, a: Assessment) -> ReviewItem:
        item = ReviewItem(a, a.at)
        self.items[a.order_id] = item
        self.bus.publish("fraud.review_opened", order_id=a.order_id, score=a.score)
        return item

    def under_review(self, order_id: str) -> bool:
        """True while the order waits for a decision (webhook delivery holds its events)."""
        item = self.items.get(order_id)
        return item is not None and item.outcome is None

    def claim(self, order_id: str, reviewer: str) -> ReviewItem:
        item = self._get(order_id)
        if item.assignee and item.assignee != reviewer and item.outcome is None:
            raise Conflict(f"{order_id} is being reviewed by {item.assignee}", order_id=order_id)
        item.assignee = reviewer
        return item

    def decide(self, order_id: str, reviewer: str, outcome: str, at: datetime, note: str = "") -> ReviewItem:
        if outcome not in (ACCEPT, REJECT):
            raise ValueError("outcome is accept or reject")
        item = self._get(order_id)
        if item.outcome is not None:
            raise Conflict(f"{order_id} was already decided ({item.outcome})", order_id=order_id)
        item.assignee, item.outcome, item.decided_at, item.note = reviewer, outcome, at, note
        self.bus.publish("fraud.review_closed", order_id=order_id, outcome=outcome, reviewer=reviewer)
        return item

    def expire(self, now: datetime) -> list[str]:
        """Accept items waiting longer than REVIEW_SLA: an unreviewed order is shipped rather than left
        hanging (the risk team agreed: a late shipment costs more than the extra fraud). Returns ids."""
        done = []
        for oid, item in self.items.items():
            if item.outcome is None and now - item.queued_at > REVIEW_SLA:
                item.outcome, item.decided_at, item.note = ACCEPT, now, "auto-accepted after SLA"
                self.bus.publish("fraud.review_closed", order_id=oid, outcome=ACCEPT, reviewer="sla")
                done.append(oid)
        return done

    def pending(self) -> list[ReviewItem]:
        return sorted((i for i in self.items.values() if i.outcome is None),
                      key=lambda i: (-i.assessment.score, i.queued_at))

    def _get(self, order_id: str) -> ReviewItem:
        try:
            return self.items[order_id]
        except KeyError:
            raise ReviewNotFound(f"no review for {order_id}", order_id=order_id) from None


def rule_stats(items: list[ReviewItem]) -> dict[str, dict[str, int]]:
    """Per signal code: how often it appeared in decided reviews and how often those were rejected
    (the risk team tunes points with this)."""
    out: dict[str, dict[str, int]] = {}
    for item in items:
        if item.outcome is None:
            continue
        for s in item.assessment.signals:
            row = out.setdefault(s.code, {"seen": 0, "rejected": 0})
            row["seen"] += 1
            row["rejected"] += item.outcome == REJECT
    return dict(sorted(out.items()))
