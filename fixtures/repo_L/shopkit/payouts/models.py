from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

ACTIVE, CLOSING, SUSPENDED, CLOSED = "active", "closing", "suspended", "closed"
DRAFT, APPROVED, SENT, FAILED, ROLLED_OVER = "draft", "approved", "sent", "failed", "rolled_over"


@dataclass
class Seller:
    id: str
    name: str
    commission_bp: int = 1500                  # 15.00%
    payout_currency: str = "USD"
    status: str = ACTIVE
    bank_account: str = ""                     # stored redacted, last 4 digits
    reserve_bp: int = 0                        # extra reserve for sellers on review (basis points of gross)
    tax_id: str = ""


@dataclass(frozen=True)
class Earning:
    """One order line sold by a seller."""
    seller_id: str
    order_id: str
    sku: str
    gross_cents: int
    delivered_on: date | None                  # None: not delivered yet
    refunded_cents: int = 0
    currency: str = "USD"


@dataclass(frozen=True)
class Adjustment:
    seller_id: str
    amount_cents: int                          # negative = deduction (chargeback, fee, correction)
    reason: str
    ref: str = ""


@dataclass
class PayoutLine:
    order_id: str
    gross_cents: int
    commission_cents: int
    refunded_cents: int
    net_cents: int


@dataclass
class Payout:
    id: str
    seller_id: str
    period_start: date
    period_end: date
    currency: str
    lines: list[PayoutLine] = field(default_factory=list)
    adjustments_cents: int = 0
    held_cents: int = 0
    reserve_cents: int = 0
    carried_in_cents: int = 0                  # rolled over from earlier periods
    status: str = DRAFT
    notes: list[str] = field(default_factory=list)

    @property
    def gross_cents(self) -> int:
        return sum(l.gross_cents for l in self.lines)

    @property
    def commission_cents(self) -> int:
        return sum(l.commission_cents for l in self.lines)

    @property
    def net_cents(self) -> int:
        return (sum(l.net_cents for l in self.lines) + self.adjustments_cents + self.carried_in_cents
                - self.held_cents - self.reserve_cents)
