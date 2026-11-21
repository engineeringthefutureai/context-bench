from dataclasses import dataclass, field
from datetime import datetime

from shopkit.utils.dates import utc_now

AUTHORIZED, CAPTURED, FAILED, VOIDED = "authorized", "captured", "failed", "voided"


@dataclass
class Payment:
    id: str
    order_id: str
    amount_cents: int
    currency: str
    method: str                      # card | paypal | gift_card
    status: str = AUTHORIZED
    card_number: str = ""            # stored redacted; see payments.reconciliation.redact_pan
    created: datetime = field(default_factory=utc_now)
    refunded_cents: int = 0

    @property
    def refundable_cents(self) -> int:
        return self.amount_cents - self.refunded_cents if self.status == CAPTURED else 0
