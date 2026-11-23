from datetime import date

from shopkit.config import Settings
from shopkit.payouts.batching import MIN_PAYOUT_CENTS, Rollover, approve, build_payout_batch, statement_text
from shopkit.payouts.holds import HoldBook
from shopkit.payouts.models import CLOSING, DRAFT, ROLLED_OVER, SUSPENDED, Adjustment, Earning
from shopkit.payouts.models import Seller as _Seller


def Seller(*a, **kw):
    kw.setdefault("tax_id", "12-3456789")
    return _Seller(*a, **kw)
from shopkit.pricing.currency import Rate, RateTable

START, END = date(2026, 3, 2), date(2026, 3, 8)
OLD = date(2026, 1, 15)          # delivered long ago: outside the return window


def _batch(sellers, earnings, adjustments=(), rollover=None, holds=None, **kw):
    return build_payout_batch(sellers, list(earnings), list(adjustments), START, END, holds=holds or HoldBook(),
                              rollover=rollover or Rollover(), **kw)


def test_commission_refunds_and_net():
    s = Seller("S1", "Pots & Co")
    res = _batch([s], [Earning("S1", "O1", "POT", 10_000, OLD), Earning("S1", "O2", "PAN", 5_000, OLD, refunded_cents=1_000)])
    p = res.payouts[0]
    assert p.commission_cents == 2_250 and p.net_cents == 10_000 - 1_500 + 5_000 - 750 - 1_000 and p.status == DRAFT


def test_full_refund_returns_commission():
    res = _batch([Seller("S1", "x")], [Earning("S1", "O1", "POT", 10_000, OLD, refunded_cents=10_000),
                                       Earning("S1", "O2", "POT", 10_000, OLD)])
    assert res.payouts[0].lines[0].net_cents == 0 and res.payouts[0].lines[0].commission_cents == 0


def test_recent_deliveries_are_held_and_released_later():
    holds = HoldBook()
    s = Seller("S1", "x")
    res = _batch([s], [Earning("S1", "O1", "POT", 10_000, date(2026, 3, 5))], holds=holds)
    assert res.payouts[0].held_cents == 8_500 and res.payouts[0].status == ROLLED_OVER     # nothing left to pay
    later = build_payout_batch([s], [], [], date(2026, 4, 6), date(2026, 4, 12), holds=holds, rollover=Rollover())
    assert later.payouts[0].adjustments_cents == 8_500


def test_small_payouts_roll_over_unless_the_seller_is_closing():
    rollover = Rollover()
    tiny = [Earning("S1", "O1", "PIN", 2_000, OLD), Earning("S2", "O2", "PIN", 2_000, OLD)]
    res = _batch([Seller("S1", "x"), Seller("S2", "y", status=CLOSING)], tiny, rollover=rollover)
    by = {p.seller_id: p for p in res.payouts}
    assert by["S1"].status == ROLLED_OVER and rollover.balances == {"S1": 1_700}
    assert by["S2"].status == DRAFT and by["S2"].net_cents == 1_700 < MIN_PAYOUT_CENTS
    nxt = _batch([Seller("S1", "x")], [Earning("S1", "O3", "PIN", 2_000, OLD)], rollover=rollover)
    assert nxt.payouts[0].carried_in_cents == 1_700 and nxt.payouts[0].status == DRAFT


def test_negative_balances_carry_forward_even_when_closing():
    rollover = Rollover()
    res = _batch([Seller("S1", "x", status=CLOSING)], [], [Adjustment("S1", -3_000, "chargeback", "CB1")], rollover=rollover)
    assert res.payouts[0].status == ROLLED_OVER and rollover.balances == {"S1": -3_000}


def test_suspended_sellers_skipped_and_bad_earnings_rejected():
    res = _batch([Seller("S1", "x", status=SUSPENDED), Seller("S2", "y")],
                 [Earning("S1", "O1", "A", 100_000, OLD), Earning("S2", "O2", "A", 5_000, OLD, refunded_cents=6_000)])
    assert res.skipped == {"S1": "suspended"} and res.stats["earnings_valid"] == 1


def test_reserve_and_currency_conversion():
    rates = RateTable()
    rates.add(Rate("USD", "EUR", END, 900_000))
    s = Seller("S1", "x", payout_currency="EUR", reserve_bp=1000)
    res = _batch([s], [Earning("S1", "O1", "A", 100_000, OLD)], rates=rates)
    p = res.payouts[0]
    assert p.reserve_cents == 10_000
    # net 75_000 USD -> 67_500 EUR - 1.5% fee (1_013) = 66_487
    assert p.net_cents == 66_487 and any("fee 1013" in n for n in p.notes)
    assert "S1" in _batch([s], [Earning("S1", "O1", "A", 100_000, OLD)]).skipped


def test_trace_flag_and_statement():
    s = Seller("S1", "Pots & Co")
    res = _batch([s], [Earning("S1", "O1", "POT", 10_000, OLD)], settings=Settings(feature_flags={"payout_trace": True}))
    p = res.payouts[0]
    approve(p, "fin")
    text = statement_text(p, s)
    assert "Pots & Co" in text and "$85.00" in text


def test_backup_withholding_for_us_sellers_without_tax_id():
    res = _batch([_Seller("S1", "x"), _Seller("S2", "y")], [Earning("S1", "O1", "A", 10_000, OLD),
                                                           Earning("S2", "O2", "A", 10_000, OLD)],
                 seller_country={"S2": "DE"})
    by = {p.seller_id: p for p in res.payouts}
    assert by["S1"].net_cents == 8_500 - 2_040 and by["S2"].net_cents == 8_500


def test_already_paid_and_duplicate_lines_are_skipped():
    metrics = {}
    e = Earning("S1", "O1", "A", 10_000, OLD)
    res = _batch([Seller("S1", "x")], [e, e, Earning("S1", "O2", "A", 10_000, OLD)],
                 paid_lines={("S1", "O2", "A")}, metrics=metrics)
    assert len(res.payouts[0].lines) == 1 and metrics["payouts.dup_in_batch"] == 1
    assert metrics["payouts.dup_already_paid"] == 1 and metrics["payouts.drafts"] == 1
    dry = _batch([Seller("S1", "x")], [e, e], settings=Settings(feature_flags={"payout_dedupe_dry_run": True}))
    assert len(dry.payouts[0].lines) == 2
