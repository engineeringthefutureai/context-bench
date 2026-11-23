"""Daily reconciliation of captured payments against the provider's settlement file."""
from dataclasses import dataclass

from shopkit.payments.models import CAPTURED, Payment
from shopkit.utils.csvio import read_csv


def redact_pan(card_number: str) -> str:
    """Card number safe to store and show: all but the last four digits masked with '*', non-digits
    dropped. redact_pan("4242 4242 4242 4242") -> "************4242". Short input is fully masked."""
    digits = "".join(c for c in card_number if c.isdigit())
    if len(digits) <= 4:
        return "*" * len(digits)
    return "*" * (len(digits) - 4) + digits[-4:]


@dataclass
class Mismatch:
    payment_id: str
    ours_cents: int | None
    theirs_cents: int | None


def reconcile(payments: list[Payment], settlement_csv: str) -> list[Mismatch]:
    """Compare captured payments with settlement rows (columns: payment_id, amount_cents).
    Returns payments missing on either side or with different amounts, sorted by id."""
    ours = {p.id: p.amount_cents - p.refunded_cents for p in payments if p.status == CAPTURED}
    theirs = {r["payment_id"]: int(r["amount_cents"]) for r in read_csv(settlement_csv)}
    out = [Mismatch(pid, ours.get(pid), theirs.get(pid))
           for pid in sorted(ours.keys() | theirs.keys()) if ours.get(pid) != theirs.get(pid)]
    return out
