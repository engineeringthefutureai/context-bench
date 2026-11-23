"""Posting rules: which journal entry each business event produces.

Every rule is idempotent through (source, source_ref) on the journal, so a replayed webhook or a
re-run of the nightly job doesn't double-book. Amounts are integer cents in the shop currency;
foreign-currency orders are converted at the order's pinned rate before they get here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from shopkit.accounting import accounts as A
from shopkit.accounting.journal import Entry, Journal, cr, dr
from shopkit.utils.numbers import apply_rate


@dataclass(frozen=True)
class SaleAmounts:
    """What checkout stored for an order (orders table, migration 0011)."""
    order_id: str
    goods_cents: int                 # before discount, without tax
    discount_cents: int
    shipping_cents: int
    tax_cents: int
    gift_card_cents: int = 0         # part of the total paid with a gift card
    store_credit_cents: int = 0

    @property
    def total_cents(self) -> int:
        return self.goods_cents - self.discount_cents + self.shipping_cents + self.tax_cents

    @property
    def card_cents(self) -> int:
        return self.total_cents - self.gift_card_cents - self.store_credit_cents


def post_sale(journal: Journal, sale: SaleAmounts, posted: date) -> Entry:
    """Order paid: money in through the provider (and gift cards / store credit), revenue, tax owed.
    Discounts are booked gross: full goods revenue and a separate contra-revenue debit."""
    lines = [
        dr(A.CASH_CLEARING, sale.card_cents),
        dr(A.GIFT_CARD_LIABILITY, sale.gift_card_cents),
        dr(A.CUSTOMER_CREDIT, sale.store_credit_cents),
        dr(A.SALES_DISCOUNTS, sale.discount_cents),
        cr(A.SALES_REVENUE, sale.goods_cents),
        cr(A.SHIPPING_REVENUE, sale.shipping_cents),
        cr(A.SALES_TAX_PAYABLE, sale.tax_cents),
    ]
    return journal.post(posted, f"Sale {sale.order_id}", lines, source="order", source_ref=sale.order_id)


def post_cogs(journal: Journal, order_id: str, cost_cents: int, posted: date) -> Entry:
    """Goods shipped: inventory leaves the books at cost."""
    return journal.post(posted, f"Cost of goods {order_id}", [dr(A.COGS, cost_cents), cr(A.INVENTORY, cost_cents)],
                        source="cogs", source_ref=order_id)


def post_refund(journal: Journal, refund_id: str, order_id: str, goods_cents: int, tax_cents: int,
                shipping_cents: int, posted: date, *, to_store_credit: bool = False) -> Entry:
    """Refund: contra-revenue for goods and shipping, the tax given back reduces tax payable. Paid back
    to the card, or as store credit when the customer chose that."""
    total = goods_cents + tax_cents + shipping_cents
    lines = [
        dr(A.REFUNDS, goods_cents + shipping_cents),
        dr(A.SALES_TAX_PAYABLE, tax_cents),
        cr(A.CUSTOMER_CREDIT if to_store_credit else A.CASH_CLEARING, total),
    ]
    return journal.post(posted, f"Refund {refund_id} for {order_id}", lines, source="refund", source_ref=refund_id)


def post_provider_fee(journal: Journal, payment_id: str, fee_cents: int, posted: date) -> Entry:
    return journal.post(posted, f"Provider fee {payment_id}", [dr(A.PAYMENT_FEES, fee_cents),
                                                               cr(A.CASH_CLEARING, fee_cents)],
                        source="fee", source_ref=payment_id)


def post_settlement(journal: Journal, settlement_id: str, amount_cents: int, posted: date) -> Entry:
    """The provider paid out to our bank: clearing -> bank."""
    return journal.post(posted, f"Settlement {settlement_id}", [dr(A.BANK, amount_cents),
                                                                cr(A.CASH_CLEARING, amount_cents)],
                        source="settlement", source_ref=settlement_id)


def post_marketplace_sale(journal: Journal, order_id: str, seller_id: str, gross_cents: int,
                          commission_bp: int, posted: date) -> Entry:
    """A seller's goods sold through us: we keep the commission, the rest is owed to the seller.
    (The customer-facing sale itself is booked by post_sale; this entry moves the seller's share
    out of our revenue.)"""
    commission = apply_rate(gross_cents, commission_bp / 100)
    lines = [dr(A.SALES_REVENUE, gross_cents), cr(A.COMMISSION_REVENUE, commission),
             cr(A.SELLER_PAYABLE, gross_cents - commission)]
    return journal.post(posted, f"Seller share {order_id} ({seller_id})", lines, source="marketplace",
                        source_ref=f"{order_id}:{seller_id}")


def post_payout(journal: Journal, payout_id: str, net_cents: int, posted: date) -> Entry:
    return journal.post(posted, f"Payout {payout_id}", [dr(A.SELLER_PAYABLE, net_cents), cr(A.BANK, net_cents)],
                        source="payout", source_ref=payout_id)


def post_gift_card_sale(journal: Journal, card_id: str, amount_cents: int, posted: date) -> Entry:
    """Selling a gift card is not revenue: it's a liability until redeemed (post_sale debits it)."""
    return journal.post(posted, f"Gift card {card_id}", [dr(A.CASH_CLEARING, amount_cents),
                                                         cr(A.GIFT_CARD_LIABILITY, amount_cents)],
                        source="gift_card", source_ref=card_id)


def post_chargeback(journal: Journal, payment_id: str, amount_cents: int, fee_cents: int, posted: date) -> Entry:
    lines = [dr(A.CHARGEBACK_LOSSES, amount_cents + fee_cents), cr(A.CASH_CLEARING, amount_cents + fee_cents)]
    return journal.post(posted, f"Chargeback {payment_id}", lines, source="chargeback", source_ref=payment_id)


def post_subscription_charge(journal: Journal, invoice_ref: str, amount_cents: int, posted: date) -> Entry:
    """A billing period is paid in advance: deferred until recognised day by day (subscriptions.revenue)."""
    return journal.post(posted, f"Subscription {invoice_ref}", [dr(A.CASH_CLEARING, amount_cents),
                                                                cr(A.DEFERRED_REVENUE, amount_cents)],
                        source="subscription", source_ref=invoice_ref)


def post_revenue_recognition(journal: Journal, ref: str, amount_cents: int, posted: date) -> Entry:
    return journal.post(posted, f"Recognise {ref}", [dr(A.DEFERRED_REVENUE, amount_cents),
                                                     cr(A.SUBSCRIPTION_REVENUE, amount_cents)],
                        source="recognition", source_ref=ref)
