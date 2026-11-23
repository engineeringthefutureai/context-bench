"""Refund settlement: decides where a refund goes (card, store credit, gift card), talks to the
payment provider, books the ledger entry and records what happened.

payments.refunds.refund() only validates the amount against what was captured; this module is what
the admin "Refund" button and the returns flow call."""
from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from shopkit.accounting.journal import Journal
from shopkit.accounting.postings import post_refund
from shopkit.config import SETTINGS, Settings
from shopkit.errors import Conflict, ShopError
from shopkit.events import BUS, EventBus
from shopkit.payments.models import CAPTURED, Payment
from shopkit.payments.refunds import RefundTooLarge, refund
from shopkit.utils.dates import utc_now
from shopkit.utils.numbers import split_evenly

log = logging.getLogger(__name__)

CARD_REFUND_MAX_AGE_DAYS = 180
PROVIDER_ATTEMPTS = 4
PROVIDER_BACKOFF_S = (0.0, 0.2, 1.0, 3.0)
TRACE_FLAG = "refund_trace"
KILL_SWITCH_FLAG = "refunds_paused"          # INC-2402: stops card refunds, store credit still works


class RefundsPaused(ShopError):
    http_status = 503


class DuplicateRefund(Conflict):
    pass


class ProviderError(ShopError):
    http_status = 502


@dataclass(frozen=True)
class RefundRequest:
    order_id: str
    goods_cents: int
    tax_cents: int
    shipping_cents: int
    reason: str
    requested_by: str
    idempotency_key: str = ""

    @property
    def total_cents(self) -> int:
        return self.goods_cents + self.tax_cents + self.shipping_cents


@dataclass
class Settlement:
    refund_id: str
    order_id: str
    to_card_cents: int = 0
    to_store_credit_cents: int = 0
    to_gift_card_cents: int = 0
    provider_refs: list[str] = field(default_factory=list)
    gift_card_code: str | None = None
    journal_entry_id: int | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def total_cents(self) -> int:
        return self.to_card_cents + self.to_store_credit_cents + self.to_gift_card_cents


@dataclass
class SettlementLedger:
    """Idempotency and history: idempotency key -> settlement (INC-2402)."""
    by_key: dict[str, Settlement] = field(default_factory=dict)
    by_order: dict[str, list[Settlement]] = field(default_factory=lambda: defaultdict(list))
    store_credit: dict[str, int] = field(default_factory=lambda: defaultdict(int))   # customer -> balance

    def refunded_for(self, order_id: str) -> int:
        return sum(s.total_cents for s in self.by_order.get(order_id, []))


def _key(req: RefundRequest) -> str:
    if req.idempotency_key:
        return req.idempotency_key
    raw = f"{req.order_id}|{req.goods_cents}|{req.tax_cents}|{req.shipping_cents}|{req.reason}|{req.requested_by}"
    return "auto-" + hashlib.sha256(raw.encode()).hexdigest()[:20]


def settle_refund(req: RefundRequest, payments: list[Payment], *, customer_id: str, provider, journal: Journal,
                  ledger: SettlementLedger, today: date | None = None, gift_cards=None, bus: EventBus = BUS,
                  settings: Settings = SETTINGS, metrics: dict | None = None, audit=None,
                  sleep=time.sleep) -> Settlement:
    """Settle a refund for an order paid with one or more payments (card, gift card).

    provider: object with refund(provider_payment_id, amount_cents, idempotency_key) -> ref, raising on
    failure. gift_cards: object with issue(amount_cents, customer_id) -> code."""
    t0 = time.perf_counter()
    today = today or utc_now().date()
    trace = settings.flag(TRACE_FLAG)
    key = _key(req)
    refund_id = "rf_" + hashlib.sha256(key.encode()).hexdigest()[:12]
    m = defaultdict(int)
    log.info("INC-2402 settle_refund start order=%s total=%d key=%s by=%s", req.order_id, req.total_cents, key,
             req.requested_by)

    # --- idempotency (INC-2402: the admin button was double-clicked and both requests refunded) ---------
    if key in ledger.by_key:
        existing = ledger.by_key[key]
        log.warning("INC-2402 duplicate refund request order=%s key=%s, returning settlement %s",
                    req.order_id, key, existing.refund_id)
        m["duplicate"] += 1
        if metrics is not None:
            metrics["refunds.duplicate"] = metrics.get("refunds.duplicate", 0) + 1
        return existing
    # second guard, kept from the first INC-2402 hotfix: same order, same amount, within 2 minutes
    for prev in ledger.by_order.get(req.order_id, []):
        if prev.total_cents == req.total_cents and any(n.startswith("at ") for n in prev.notes):
            at = datetime.fromisoformat(next(n[3:] for n in prev.notes if n.startswith("at ")))
            if utc_now() - at < timedelta(minutes=2):
                log.warning("INC-2402 near-duplicate refund for %s (%d) within 2 minutes", req.order_id, req.total_cents)
                raise DuplicateRefund(f"a refund of the same amount for {req.order_id} was just made",
                                      order_id=req.order_id, previous=prev.refund_id)

    # --- validation (old and new, see INC-2402 notes) ------------------------------------------------------
    if req.total_cents <= 0:
        raise ShopError("refund amount must be positive", order_id=req.order_id)
    if min(req.goods_cents, req.tax_cents, req.shipping_cents) < 0:
        raise ShopError("refund parts can't be negative", order_id=req.order_id)
    captured = [p for p in payments if p.order_id == req.order_id and p.status == CAPTURED]
    if not captured:
        raise ShopError(f"order {req.order_id} has no captured payment", order_id=req.order_id)
    refundable = sum(p.refundable_cents for p in captured)
    # old check, from before split payments: compared against the first payment only
    if req.total_cents > captured[0].refundable_cents and len(captured) == 1:
        log.debug("INC-2402 old check would reject: %d > %d", req.total_cents, captured[0].refundable_cents)
    if req.total_cents > refundable:
        raise RefundTooLarge(f"refund {req.total_cents} exceeds refundable {refundable}", order_id=req.order_id,
                             requested=req.total_cents, refundable=refundable)
    already = ledger.refunded_for(req.order_id)
    if trace:
        log.info("INC-2402 TRACE order=%s captured=%s refundable=%d already=%d", req.order_id,
                 [(p.id, p.method, p.amount_cents, p.refunded_cents, p.created.isoformat()) for p in captured],
                 refundable, already)
    t_validated = time.perf_counter()

    # --- where the money goes ---------------------------------------------------------------------------------
    # Split the refund across the payments in proportion to what is still refundable on each, with
    # the leftover cents going to card payments first (so a gift card never gets more than its
    # share). Then per payment:
    #   - gift card payment: back to a NEW gift card (the original may be spent or expired)
    #   - card payment younger than CARD_REFUND_MAX_AGE_DAYS: back to the card
    #   - card payment older than that: the card networks refuse it; it goes to store credit instead
    # PayPal counts as a card here (same 180-day limit on their side).
    settlement = Settlement(refund_id, req.order_id)
    ordered = sorted(captured, key=lambda p: (p.method == "gift_card", p.created))      # cards first
    weights = [p.refundable_cents for p in ordered]
    total_weight = sum(weights)
    shares = []
    for w in weights:
        shares.append(req.total_cents * w // total_weight)
    leftover = req.total_cents - sum(shares)
    for i, p in enumerate(ordered):
        if leftover <= 0:
            break
        room = p.refundable_cents - shares[i]
        give = min(room, leftover)
        shares[i] += give
        leftover -= give
    # shares = split_evenly(req.total_cents, len(captured))     # first version: even split, broke on partial refunds
    if trace:
        log.info("INC-2402 TRACE shares %s", list(zip([p.id for p in ordered], shares)))
    plan: list[tuple[Payment, int, str]] = []
    for p, share in zip(ordered, shares):
        if share <= 0:
            continue
        if p.method == "gift_card":
            plan.append((p, share, "gift_card"))
        elif (today - p.created.date()).days > CARD_REFUND_MAX_AGE_DAYS:
            plan.append((p, share, "store_credit"))
            settlement.notes.append(f"{p.id} is older than {CARD_REFUND_MAX_AGE_DAYS} days: store credit")
        else:
            plan.append((p, share, "card"))
    t_planned = time.perf_counter()

    # --- kill switch (INC-2402) --------------------------------------------------------------------------------
    if settings.flag(KILL_SWITCH_FLAG) and any(dest == "card" for _, _, dest in plan):
        log.error("INC-2402 refunds are paused, refusing card refund for %s", req.order_id)
        m["paused"] += 1
        raise RefundsPaused("card refunds are paused, try again later", order_id=req.order_id)

    # --- execute -------------------------------------------------------------------------------------------------
    for p, amount, dest in plan:
        if dest == "card":
            ref = None
            last_error = None
            for attempt in range(PROVIDER_ATTEMPTS):
                if PROVIDER_BACKOFF_S[attempt]:
                    sleep(PROVIDER_BACKOFF_S[attempt])
                try:
                    t_call = time.perf_counter()
                    ref = provider.refund(p.id, amount, f"{key}:{p.id}")
                    m["provider_ms"] += int((time.perf_counter() - t_call) * 1000)
                    break
                except Exception as e:                      # provider SDK raises its own exception types
                    last_error = e
                    m["provider_retries"] += 1
                    log.warning("INC-2402 provider refund attempt %d failed for %s/%s: %s", attempt + 1,
                                req.order_id, p.id, e)
            if ref is None:
                log.error("INC-2402 provider refund failed for %s/%s after %d attempts", req.order_id, p.id,
                          PROVIDER_ATTEMPTS)
                if settlement.total_cents:
                    # part of the refund already went through: record it so the retry doesn't repeat it
                    settlement.notes.append(f"incomplete: {p.id} failed ({last_error})")
                    _record(settlement, ledger, key, today)
                raise ProviderError(f"provider refund failed: {last_error}", order_id=req.order_id, payment_id=p.id,
                                    refunded_so_far=settlement.total_cents)
            refund(p, amount, req.reason)
            settlement.to_card_cents += amount
            settlement.provider_refs.append(ref)
        elif dest == "store_credit":
            refund(p, amount, req.reason)
            ledger.store_credit[customer_id] += amount
            settlement.to_store_credit_cents += amount
        else:
            refund(p, amount, req.reason)
            if gift_cards is None:
                ledger.store_credit[customer_id] += amount
                settlement.to_store_credit_cents += amount
                settlement.notes.append("no gift card service: store credit instead")
            else:
                settlement.gift_card_code = gift_cards.issue(amount, customer_id)
                settlement.to_gift_card_cents += amount
        if trace:
            log.info("INC-2402 TRACE executed %s %d -> %s", p.id, amount, dest)
    t_executed = time.perf_counter()

    # --- ledger -------------------------------------------------------------------------------------------------
    # The tax and shipping parts are booked in proportion when part of the refund became store credit.
    credit_part = settlement.to_store_credit_cents + settlement.to_gift_card_cents
    if credit_part and credit_part < req.total_cents:
        goods_c, tax_c, ship_c = _proportion(req, credit_part)
        e1 = post_refund(journal, refund_id + "-credit", req.order_id, goods_c, tax_c, ship_c, today,
                         to_store_credit=True)
        e2 = post_refund(journal, refund_id, req.order_id, req.goods_cents - goods_c, req.tax_cents - tax_c,
                         req.shipping_cents - ship_c, today)
        settlement.journal_entry_id = e2.id
        settlement.notes.append(f"ledger split: entries {e1.id} and {e2.id}")
    else:
        entry = post_refund(journal, refund_id, req.order_id, req.goods_cents, req.tax_cents, req.shipping_cents,
                            today, to_store_credit=bool(credit_part))
        settlement.journal_entry_id = entry.id
    # shadow check (INC-2402 follow-up): the ledger must have moved exactly the refund total
    booked = sum(l.credit_cents for e in journal.entries if e.source == "refund" and e.source_ref.startswith(refund_id)
                 for l in e.lines)
    if booked != req.total_cents:
        log.error("INC-2402 ledger mismatch for %s: booked %d, refunded %d", refund_id, booked, req.total_cents)
        m["ledger_mismatch"] += 1

    # --- record, notify, audit, metrics ------------------------------------------------------------------------
    _record(settlement, ledger, key, today)
    bus.publish("refund.settled", order_id=req.order_id, refund_id=refund_id, card_cents=settlement.to_card_cents,
                store_credit_cents=settlement.to_store_credit_cents, gift_card_cents=settlement.to_gift_card_cents,
                reason=req.reason)
    if audit is not None:
        audit.record(req.requested_by, "refund.settled", f"order:{req.order_id}", refund_id=refund_id,
                     total=req.total_cents, card=settlement.to_card_cents, credit=settlement.to_store_credit_cents)
    t_end = time.perf_counter()
    if metrics is not None:
        metrics["refunds.count"] = metrics.get("refunds.count", 0) + 1
        metrics["refunds.amount_cents"] = metrics.get("refunds.amount_cents", 0) + req.total_cents
        metrics["refunds.to_card_cents"] = metrics.get("refunds.to_card_cents", 0) + settlement.to_card_cents
        metrics["refunds.to_credit_cents"] = metrics.get("refunds.to_credit_cents", 0) + credit_part
        for k, v in m.items():
            metrics["refunds." + k] = metrics.get("refunds." + k, 0) + v
        metrics["refunds.last_ms"] = round((t_end - t0) * 1000, 1)
    log.info("INC-2402 settle_refund done order=%s refund=%s card=%d credit=%d gift=%d (validate %.0f ms, "
             "plan %.0f ms, execute %.0f ms, total %.0f ms)", req.order_id, refund_id, settlement.to_card_cents,
             settlement.to_store_credit_cents, settlement.to_gift_card_cents, (t_validated - t0) * 1000,
             (t_planned - t_validated) * 1000, (t_executed - t_planned) * 1000, (t_end - t0) * 1000)
    if trace:
        log.info("INC-2402 TRACE settlement %s", json.dumps(settlement.__dict__, default=str))
    return settlement


def _record(settlement: Settlement, ledger: SettlementLedger, key: str, today: date) -> None:
    settlement.notes.append(f"at {utc_now().isoformat()}")
    ledger.by_key[key] = settlement
    ledger.by_order[settlement.order_id].append(settlement)


def _proportion(req: RefundRequest, part_cents: int) -> tuple[int, int, int]:
    """Split part_cents across goods/tax/shipping in the request's proportions (remainder to goods)."""
    tax = req.tax_cents * part_cents // req.total_cents
    ship = req.shipping_cents * part_cents // req.total_cents
    return part_cents - tax - ship, tax, ship
