"""Credit notes: the accounting document for refunds. Negative amounts, own number sequence."""
from dataclasses import dataclass
from datetime import date

from shopkit.invoices.numbering import next_invoice_number
from shopkit.money import format_money


@dataclass(frozen=True)
class CreditNote:
    number: str
    invoice_number: str
    amount_cents: int            # negative
    currency: str
    issued: date
    reason: str

    def summary(self) -> str:
        return (f"Credit note {self.number} for invoice {self.invoice_number}: "
                f"{format_money(self.amount_cents, self.currency)} ({self.reason})")


def issue(last_number: str | None, invoice_number: str, refund_cents: int, currency: str,
          issued: date, reason: str) -> CreditNote:
    number = next_invoice_number(last_number, issued.year, prefix="CN")
    return CreditNote(number, invoice_number, -abs(refund_cents), currency, issued, reason)
