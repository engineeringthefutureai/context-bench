from datetime import date

import pytest

from shopkit.accounting import accounts as A
from shopkit.accounting.accounts import Account, Chart, UnknownAccount
from shopkit.accounting.journal import Journal, PeriodClosed, Unbalanced, cr, dr
from shopkit.accounting.periods import CloseBlocked, balance_sheet, close_month, profit_and_loss, trial_balance
from shopkit.accounting.postings import (SaleAmounts, post_cogs, post_gift_card_sale, post_marketplace_sale,
                                         post_payout, post_refund, post_sale, post_settlement)

D = date(2026, 3, 10)


def test_entries_must_balance_and_use_known_accounts():
    j = Journal()
    with pytest.raises(Unbalanced):
        j.post(D, "x", [dr(A.BANK, 100), cr(A.SALES_REVENUE, 90)])
    with pytest.raises(UnknownAccount):
        j.post(D, "x", [dr("1999", 100), cr(A.SALES_REVENUE, 100)])
    with pytest.raises(ValueError):
        dr(A.BANK, -5)


def test_sale_posting_is_idempotent_and_books_discount_gross():
    j = Journal()
    sale = SaleAmounts("O1", goods_cents=10_000, discount_cents=1_000, shipping_cents=500, tax_cents=720,
                       gift_card_cents=2_000)
    e1 = post_sale(j, sale, D)
    assert post_sale(j, sale, D) is e1 and len(j.entries) == 1
    assert j.balance(A.CASH_CLEARING.code) == 8_220 and j.balance(A.GIFT_CARD_LIABILITY.code) == -2_000
    assert j.balance(A.SALES_REVENUE.code) == 10_000 and j.balance(A.SALES_DISCOUNTS.code) == -1_000


def test_refund_reverses_tax_and_can_go_to_store_credit():
    j = Journal()
    post_refund(j, "R1", "O1", goods_cents=2_000, tax_cents=160, shipping_cents=0, posted=D, to_store_credit=True)
    assert j.balance(A.CUSTOMER_CREDIT.code) == 2_160 and j.balance(A.SALES_TAX_PAYABLE.code) == -160


def test_marketplace_commission_and_payout():
    j = Journal()
    post_marketplace_sale(j, "O7", "S1", gross_cents=10_000, commission_bp=1500, posted=D)
    assert j.balance(A.COMMISSION_REVENUE.code) == 1_500 and j.balance(A.SELLER_PAYABLE.code) == 8_500
    post_payout(j, "P1", 8_500, D)
    assert j.balance(A.SELLER_PAYABLE.code) == 0


def test_reversal_and_double_reversal():
    j = Journal()
    e = j.post(D, "typo", [dr(A.BANK, 100), cr(A.SALES_REVENUE, 100)])
    j.reverse(e.id, D)
    assert j.balance(A.BANK.code) == 0
    with pytest.raises(Exception):
        j.reverse(e.id, D)


def test_reports_and_month_close():
    j = Journal()
    post_sale(j, SaleAmounts("O1", 10_000, 0, 0, 800), date(2026, 3, 5))
    post_cogs(j, "O1", 4_000, date(2026, 3, 6))
    post_gift_card_sale(j, "G1", 5_000, date(2026, 3, 7))
    post_settlement(j, "ST1", 9_000, date(2026, 3, 8))
    assert trial_balance(j, date(2026, 3, 31)).balanced
    pl = profit_and_loss(j, date(2026, 3, 1), date(2026, 3, 31))
    assert (pl.net_revenue_cents, pl.expenses_cents, pl.profit_cents) == (10_000, 4_000, 6_000)
    sheet = balance_sheet(j, date(2026, 3, 31))
    assert sheet[A.ASSET] == sheet[A.LIABILITY] + sheet[A.EQUITY]
    with pytest.raises(CloseBlocked):
        close_month(j, 2026, 3, open_items=2)
    close_month(j, 2026, 3)
    assert j.balance(A.SALES_REVENUE.code) == 0 and j.balance(A.RETAINED_EARNINGS.code) == 6_000
    with pytest.raises(PeriodClosed):
        j.post(date(2026, 3, 31), "late", [dr(A.BANK, 1), cr(A.SALES_REVENUE, 1)])
    with pytest.raises(CloseBlocked, match="close April first"):
        close_month(j, 2026, 5)


def test_chart_rules():
    chart = Chart()
    with pytest.raises(ValueError):
        chart.add(Account("12", "short", A.ASSET))
    assert [a.code for a in chart.by_kind(A.EQUITY)] == ["3100"]
