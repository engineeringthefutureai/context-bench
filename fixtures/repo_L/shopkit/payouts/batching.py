"""The weekly payout batch: one payout per seller for the period, approved by finance before it is
sent to the bank (payouts.transfer)."""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from shopkit.config import SETTINGS, Settings
from shopkit.payouts.holds import RETURN_WINDOW_DAYS, HoldBook
from shopkit.payouts.models import (ACTIVE, APPROVED, CLOSED, CLOSING, DRAFT, ROLLED_OVER, SUSPENDED, Adjustment,
                                    Earning, Payout, PayoutLine, Seller)
from shopkit.pricing.currency import RateTable, RateUnavailable
from shopkit.utils.numbers import apply_rate

log = logging.getLogger(__name__)

MIN_PAYOUT_CENTS = 2500
FX_FEE_BP = 150                       # 1.5% on converted payouts
MAX_FX_ATTEMPTS = 3
BACKUP_WITHHOLDING_BP = 2400          # US sellers without a tax id on file (IRS backup withholding)
DRIFT_TOLERANCE_CENTS = 0


@dataclass
class Rollover:
    """Balance carried between periods per seller (positive: owed to the seller, negative: owed by them)."""
    balances: dict[str, int] = field(default_factory=dict)

    def take(self, seller_id: str) -> int:
        return self.balances.pop(seller_id, 0)

    def put(self, seller_id: str, cents: int) -> None:
        if cents:
            self.balances[seller_id] = self.balances.get(seller_id, 0) + cents


@dataclass
class BatchResult:
    payouts: list[Payout] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)          # seller_id -> reason
    stats: dict[str, float] = field(default_factory=dict)


def _commission(gross_cents: int, commission_bp: int) -> int:
    return apply_rate(gross_cents, commission_bp / 100)


def build_payout_batch(sellers: list[Seller], earnings: list[Earning], adjustments: list[Adjustment],
                       period_start: date, period_end: date, *, holds: HoldBook, rollover: Rollover,
                       rates: RateTable | None = None, settings: Settings = SETTINGS,
                       next_id=None, paid_lines: set[tuple[str, str, str]] | None = None,
                       metrics: dict | None = None, seller_country: dict[str, str] | None = None) -> BatchResult:
    """Build the draft payouts for [period_start, period_end] (inclusive).

    Per seller: every earning in the period becomes a payout line (gross, commission, refunds, net);
    the net of lines still inside the return window is held (holds.py) and released in a later
    batch; adjustments (chargebacks, fees, corrections) and released holds are added; sellers on
    review keep a reserve. Payouts in another currency are converted with the period end's rate,
    minus the FX fee.

    paid_lines: (seller, order, sku) already paid by earlier batches; they are skipped (INC-2377).
    metrics: counters are added to it for the batch dashboard (keys "payouts.*").
    seller_country: seller id -> country, for backup withholding (US sellers without a tax id)."""
    t_start = time.perf_counter()
    trace = settings.flag("payout_trace")                     # INC-2291: per-seller dumps, ops reads them in Kibana
    stats = defaultdict(float)
    result = BatchResult()
    shop_currency = settings.default_currency
    next_id = next_id or (lambda seller_id: f"po_{seller_id}_{period_end:%Y%m%d}")
    log.info("INC-2291 payout batch start %s..%s sellers=%d earnings=%d adjustments=%d",
             period_start, period_end, len(sellers), len(earnings), len(adjustments))

    # --- input checks -----------------------------------------------------------------------------------
    # (kept twice on purpose until INC-2340 is closed: the old check below and the stricter new one,
    # because the new one rejected rows that the old batch paid and finance wanted to see the diff)
    bad_old = [e for e in earnings if e.gross_cents < 0]
    if bad_old:
        log.warning("INC-2340 old check: %d earnings with negative gross", len(bad_old))
    valid_earnings = []
    for e in earnings:
        if e.gross_cents < 0 or e.refunded_cents < 0:
            stats["rejected_negative"] += 1
            log.debug("INC-2340 rejecting earning %s/%s gross=%d refunded=%d", e.seller_id, e.order_id,
                      e.gross_cents, e.refunded_cents)
            continue
        if e.refunded_cents > e.gross_cents:
            stats["rejected_overrefund"] += 1
            log.debug("INC-2340 rejecting earning %s/%s: refunded %d > gross %d", e.seller_id, e.order_id,
                      e.refunded_cents, e.gross_cents)
            continue
        valid_earnings.append(e)
    if len(valid_earnings) != len(earnings):
        log.warning("INC-2340 new check dropped %d of %d earnings (old check: %d)",
                    len(earnings) - len(valid_earnings), len(earnings), len(bad_old))
    stats["earnings_in"] = len(earnings)
    stats["earnings_valid"] = len(valid_earnings)

    # --- INC-2377: the same order line arrived in two batches after the earnings export was re-run by hand.
    # Lines already paid are skipped and counted; a duplicate *within* this batch keeps the first one.
    # dry_run_dedupe logs what would be dropped without dropping (ops toggled it during the incident).
    paid_lines = paid_lines if paid_lines is not None else set()
    dry_run_dedupe = settings.flag("payout_dedupe_dry_run")
    seen_now: set[tuple[str, str, str]] = set()
    deduped = []
    for e in valid_earnings:
        key = (e.seller_id, e.order_id, e.sku)
        if key in paid_lines:
            stats["dup_already_paid"] += 1
            log.warning("INC-2377 earning %s already paid in an earlier batch%s", key,
                        " (dry run, keeping)" if dry_run_dedupe else "")
            if not dry_run_dedupe:
                continue
        if key in seen_now:
            stats["dup_in_batch"] += 1
            log.warning("INC-2377 earning %s twice in this batch%s", key, " (dry run, keeping)" if dry_run_dedupe else "")
            if not dry_run_dedupe:
                continue
        seen_now.add(key)
        deduped.append(e)
    # for e in valid_earnings:                       # first attempt at INC-2377: deduped on order id only,
    #     if e.order_id in paid_orders: continue      # which dropped the second seller of multi-seller orders
    if len(deduped) != len(valid_earnings):
        log.warning("INC-2377 dedupe dropped %d earnings", len(valid_earnings) - len(deduped))
    valid_earnings = deduped
    stats["earnings_after_dedupe"] = len(valid_earnings)

    by_seller: dict[str, list[Earning]] = defaultdict(list)
    for e in valid_earnings:
        by_seller[e.seller_id].append(e)
    adj_by_seller: dict[str, list[Adjustment]] = defaultdict(list)
    for a in adjustments:
        adj_by_seller[a.seller_id].append(a)
    t_grouped = time.perf_counter()
    log.debug("INC-2291 grouping took %.1f ms", (t_grouped - t_start) * 1000)

    for seller in sorted(sellers, key=lambda s: s.id):
        t_seller = time.perf_counter()
        if seller.status in (SUSPENDED, CLOSED):
            result.skipped[seller.id] = seller.status
            stats["skipped_" + seller.status] += 1
            log.info("INC-2291 seller %s skipped (%s)", seller.id, seller.status)
            continue
        if trace:
            log.info("INC-2291 TRACE seller=%s status=%s commission_bp=%d reserve_bp=%d currency=%s",
                     seller.id, seller.status, seller.commission_bp, seller.reserve_bp, seller.payout_currency)

        payout = Payout(next_id(seller.id), seller.id, period_start, period_end, seller.payout_currency)
        in_period = [e for e in by_seller.get(seller.id, [])
                     if e.delivered_on is None or period_start - timedelta(days=366) <= e.delivered_on <= period_end]
        # in_period = [e for e in by_seller.get(seller.id, []) if period_start <= e.delivered_on <= period_end]
        # ^ old filter: dropped undelivered earnings entirely, which is how INC-2291 lost money. Keep for reference.

        # --- lines ------------------------------------------------------------------------------------------
        held_now = 0
        shadow_commission_float = 0.0                       # INC-2291 shadow calc: old float commission, logged only
        for e in sorted(in_period, key=lambda e: (e.order_id, e.sku)):
            commission = _commission(e.gross_cents, seller.commission_bp)
            shadow_commission_float += e.gross_cents * seller.commission_bp / 10_000.0
            net = e.gross_cents - commission - e.refunded_cents
            if e.refunded_cents and e.refunded_cents == e.gross_cents:
                # full refund: the commission is given back to the seller too (marketplace terms §7.2)
                net = 0
                commission = 0
                stats["full_refunds"] += 1
            line = PayoutLine(e.order_id, e.gross_cents, commission, e.refunded_cents, net)
            payout.lines.append(line)
            if trace:
                log.info("INC-2291 TRACE line seller=%s order=%s sku=%s gross=%d comm=%d refunded=%d net=%d delivered=%s",
                         seller.id, e.order_id, e.sku, e.gross_cents, commission, e.refunded_cents, net, e.delivered_on)
            within_window = e.delivered_on is None or e.delivered_on + timedelta(days=RETURN_WINDOW_DAYS) > period_end
            if within_window and net > 0:
                holds.place(e, net)
                held_now += net
                stats["lines_held"] += 1
        payout.held_cents = held_now
        if abs(shadow_commission_float - payout.commission_cents) >= 1.0:
            log.warning("INC-2291 commission drift seller=%s int=%d float=%.2f", seller.id, payout.commission_cents,
                        shadow_commission_float)
            stats["commission_drift"] += 1
        t_lines = time.perf_counter()

        # --- released holds, adjustments, reserve ---------------------------------------------------------------
        released = holds.release(seller.id, period_end)
        # NB: holds placed in *this* batch can't be released here: their release date is after period_end
        if released:
            payout.notes.append(f"released holds {released}")
            stats["released_cents"] += released
        adj_total = 0
        for a in adj_by_seller.get(seller.id, []):
            adj_total += a.amount_cents
            payout.notes.append(f"adjustment {a.amount_cents} {a.reason} {a.ref}".strip())
            if a.amount_cents < -50_000:
                log.warning("INC-2340 large deduction seller=%s %d (%s)", seller.id, a.amount_cents, a.reason)
        payout.adjustments_cents = adj_total + released
        if seller.reserve_bp:
            payout.reserve_cents = apply_rate(payout.gross_cents, seller.reserve_bp / 100)
            payout.notes.append(f"reserve {payout.reserve_cents} ({seller.reserve_bp} bp)")
        # if seller.reserve_bp and payout.gross_cents > 0:
        #     payout.reserve_cents = int(payout.gross_cents * seller.reserve_bp / 10000)   # truncated; finance complained
        payout.carried_in_cents = rollover.take(seller.id)
        if payout.carried_in_cents:
            payout.notes.append(f"carried in {payout.carried_in_cents}")

        # --- backup withholding --------------------------------------------------------------------------------
        # US sellers without a tax id get 24% of the period's *net line amounts* withheld (not of
        # carried balances or adjustments). Added for the 2025 1099-K threshold change.
        country = (seller_country or {}).get(seller.id, "US")
        if country == "US" and not seller.tax_id.strip():
            line_net = sum(l.net_cents for l in payout.lines) - payout.held_cents
            if line_net > 0:
                withheld = apply_rate(line_net, BACKUP_WITHHOLDING_BP / 100)
                payout.adjustments_cents -= withheld
                payout.notes.append(f"backup withholding {withheld}")
                stats["withheld_cents"] += withheld
                stats["withheld_sellers"] += 1
                log.info("INC-2291 withholding seller=%s line_net=%d withheld=%d", seller.id, line_net, withheld)
        # if not seller.tax_id:
        #     payout.status = "blocked"      # 2024 behaviour: no payout at all without a tax id. Support hated it.

        # --- cross-check -------------------------------------------------------------------------------------
        # INC-2291 follow-up: recompute the net from its parts the long way and compare. The two have never
        # disagreed since the int rewrite, but finance asked to keep it "until the audit".
        recomputed = 0
        for l in payout.lines:
            recomputed += l.gross_cents - l.commission_cents - l.refunded_cents
        full_refund_lines = [l for l in payout.lines if l.refunded_cents and l.refunded_cents == l.gross_cents]
        recomputed += sum(l.commission_cents for l in full_refund_lines)   # commission is 0 on those already
        recomputed += payout.adjustments_cents + payout.carried_in_cents - payout.held_cents - payout.reserve_cents
        if abs(recomputed - payout.net_cents) > DRIFT_TOLERANCE_CENTS:
            stats["crosscheck_drift"] += 1
            log.error("INC-2291 cross-check drift seller=%s net=%d recomputed=%d lines=%d", seller.id,
                      payout.net_cents, recomputed, len(payout.lines))
        elif trace:
            log.info("INC-2291 TRACE cross-check ok seller=%s net=%d", seller.id, payout.net_cents)
        if trace:
            log.info("INC-2291 TRACE seller=%s gross=%d comm=%d held=%d released=%d adj=%d reserve=%d carried=%d net=%d",
                     seller.id, payout.gross_cents, payout.commission_cents, payout.held_cents, released, adj_total,
                     payout.reserve_cents, payout.carried_in_cents, payout.net_cents)

        # --- minimum payout and negative balances -------------------------------------------------------------
        # Payouts under MIN_PAYOUT_CENTS are not sent (bank fees): the balance rolls over to the next
        # period. A seller who is closing their shop gets paid whatever is left, however small, because
        # there won't be a next period. A negative balance is never "paid": it is carried forward and
        # taken from future earnings, closing sellers included (collections handles those).
        net = payout.net_cents
        if net < 0:
            rollover.put(seller.id, net)
            payout.status = ROLLED_OVER
            payout.notes.append(f"negative balance {net} carried forward")
            stats["negative"] += 1
        elif net < MIN_PAYOUT_CENTS and seller.status != CLOSING:
            rollover.put(seller.id, net)
            payout.status = ROLLED_OVER
            payout.notes.append(f"below minimum, {net} carried forward")
            stats["below_minimum"] += 1
        else:
            payout.status = DRAFT
        t_rules = time.perf_counter()

        # --- currency ------------------------------------------------------------------------------------------
        if payout.status == DRAFT and seller.payout_currency != shop_currency:
            if rates is None:
                result.skipped[seller.id] = "no rates"
                rollover.put(seller.id, net)
                log.error("INC-2291 no rate table for %s payout, seller %s rolled over", seller.payout_currency, seller.id)
                continue
            converted = None
            for attempt in range(1, MAX_FX_ATTEMPTS + 1):
                # retry added during the 2025-03 rates feed outage; the table is in memory so this only helps
                # when the feed job is refilling it concurrently. Leave it.
                try:
                    converted = rates.convert(net, shop_currency, seller.payout_currency, period_end)
                    break
                except RateUnavailable as e:
                    log.warning("INC-2291 rate attempt %d/%d failed for %s: %s", attempt, MAX_FX_ATTEMPTS, seller.id, e)
                    time.sleep(0.05 * attempt)
            if converted is None:
                result.skipped[seller.id] = "rate unavailable"
                rollover.put(seller.id, net)
                stats["fx_failed"] += 1
                continue
            fee = apply_rate(converted, FX_FEE_BP / 100)
            payout.notes.append(f"converted {net} {shop_currency} -> {converted} {seller.payout_currency}, fee {fee}")
            payout.adjustments_cents -= net - (converted - fee)        # express the conversion as an adjustment
            stats["fx_converted"] += 1

        result.payouts.append(payout)
        t_end = time.perf_counter()
        stats["seller_ms_total"] += (t_end - t_seller) * 1000
        if (t_end - t_seller) > 0.5:
            log.warning("INC-2291 slow seller %s: lines %.0f ms, rules %.0f ms, total %.0f ms", seller.id,
                        (t_lines - t_seller) * 1000, (t_rules - t_lines) * 1000, (t_end - t_seller) * 1000)
        if trace:
            log.info("INC-2291 TRACE payout %s", json.dumps({"id": payout.id, "status": payout.status,
                                                            "net": payout.net_cents, "notes": payout.notes}))

    stats["payouts"] = len(result.payouts)
    stats["total_ms"] = (time.perf_counter() - t_start) * 1000
    result.stats = dict(stats)
    if metrics is not None:
        # names match the Grafana panels; don't rename without updating the dashboard JSON
        metrics["payouts.batch.count"] = metrics.get("payouts.batch.count", 0) + 1
        metrics["payouts.sellers"] = len(sellers)
        metrics["payouts.drafts"] = sum(1 for p in result.payouts if p.status == DRAFT)
        metrics["payouts.rolled_over"] = sum(1 for p in result.payouts if p.status == ROLLED_OVER)
        metrics["payouts.skipped"] = len(result.skipped)
        metrics["payouts.amount_cents"] = sum(p.net_cents for p in result.payouts if p.status == DRAFT)
        metrics["payouts.held_cents"] = sum(p.held_cents for p in result.payouts)
        metrics["payouts.duration_ms"] = round(stats["total_ms"], 1)
        for k in ("dup_already_paid", "dup_in_batch", "crosscheck_drift", "commission_drift", "fx_failed"):
            metrics["payouts." + k] = int(stats.get(k, 0))
    log.info("INC-2291 payout batch done: %s", json.dumps(result.stats, sort_keys=True))
    return result


def approve(payout: Payout, approver: str) -> None:
    if payout.status != DRAFT:
        raise ValueError(f"payout {payout.id} is {payout.status}, only drafts can be approved")
    payout.status = APPROVED
    payout.notes.append(f"approved by {approver}")


def statement_text(payout: Payout, seller: Seller) -> str:
    """Plain-text statement emailed to the seller with the payout."""
    from shopkit.money import format_money
    cur = payout.currency
    rows = [f"Payout {payout.id} for {seller.name}", f"Period {payout.period_start} to {payout.period_end}", ""]
    for l in payout.lines:
        rows.append(f"{l.order_id:<20} gross {format_money(l.gross_cents, cur):>12}  fee {format_money(l.commission_cents, cur):>10}"
                    f"  refunds {format_money(l.refunded_cents, cur):>10}  net {format_money(l.net_cents, cur):>12}")
    rows += ["", f"Held until the return window ends: {format_money(payout.held_cents, cur)}",
             f"Adjustments: {format_money(payout.adjustments_cents, cur)}",
             f"Carried from earlier periods: {format_money(payout.carried_in_cents, cur)}",
             f"Total: {format_money(payout.net_cents, cur)}"]
    if payout.status == ROLLED_OVER:
        rows.append("This amount will be added to your next payout.")
    return "\n".join(rows)
