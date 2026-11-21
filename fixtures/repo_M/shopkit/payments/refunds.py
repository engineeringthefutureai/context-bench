"""Refunds against captured payments. Partial refunds are allowed up to the captured amount."""
from dataclasses import dataclass, field
from datetime import datetime

from shopkit.errors import Conflict
from shopkit.money import format_money
from shopkit.payments.models import Payment
from shopkit.utils.dates import utc_now


class RefundTooLarge(Conflict):
    pass


@dataclass(frozen=True)
class Refund:
    payment_id: str
    amount_cents: int
    reason: str
    at: datetime = field(default_factory=utc_now)


def refund(payment: Payment, amount_cents: int, reason: str) -> Refund:
    if amount_cents <= 0:
        raise ValueError("refund amount must be positive")
    if amount_cents > payment.refundable_cents:
        raise RefundTooLarge(
            f"refund {format_money(amount_cents, payment.currency)} exceeds refundable "
            f"{format_money(payment.refundable_cents, payment.currency)}",
            payment_id=payment.id, requested=amount_cents, refundable=payment.refundable_cents)
    payment.refunded_cents += amount_cents
    return Refund(payment.id, amount_cents, reason)
