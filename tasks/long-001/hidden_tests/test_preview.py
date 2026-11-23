import copy
from datetime import date

from shopkit.payouts.batching import Rollover, build_payout_batch
from shopkit.payouts.holds import HoldBook
from shopkit.payouts.models import Adjustment, Earning, Seller
from shopkit.payouts.preview import preview_payout

START, END, OLD = date(2026, 3, 2), date(2026, 3, 8), date(2026, 1, 15)


def test_small_payout_rolls_over_but_closing_seller_is_paid():
    e = [Earning("S1", "O1", "PIN", 2_000, OLD)]
    assert preview_payout(Seller("S1", "x", tax_id="1"), e, [], START, END, HoldBook(), Rollover()) == ("rolled_over", 1_700)
    closing = Seller("S1", "x", tax_id="1", status="closing")
    assert preview_payout(closing, e, [], START, END, HoldBook(), Rollover()) == ("draft", 1_700)


def test_negative_balance_and_carried_amount():
    s = Seller("S1", "x", tax_id="1", status="closing")
    assert preview_payout(s, [], [Adjustment("S1", -3_000, "chargeback")], START, END, HoldBook(), Rollover()) == ("rolled_over", -3_000)
    r = Rollover({"S1": 1_000})
    assert preview_payout(Seller("S1", "x", tax_id="1"), [Earning("S1", "O1", "PIN", 2_000, OLD)], [], START, END,
                          HoldBook(), r) == ("draft", 2_700)


def test_preview_changes_nothing_and_matches_the_batch():
    seller = Seller("S1", "x", tax_id="1")
    earnings = [Earning("S1", "O1", "POT", 10_000, date(2026, 3, 5)), Earning("S1", "O2", "PAN", 40_000, OLD)]
    holds, rollover = HoldBook(), Rollover({"S1": 500})
    result = preview_payout(seller, earnings, [], START, END, holds, rollover)
    assert holds.holds == {} and rollover.balances == {"S1": 500}
    batch = build_payout_batch([seller], earnings, [], START, END, holds=copy.deepcopy(holds),
                               rollover=copy.deepcopy(rollover))
    assert result == (batch.payouts[0].status, batch.payouts[0].net_cents)
