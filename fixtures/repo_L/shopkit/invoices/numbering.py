"""Invoice numbers: gap-free per year, e.g. INV-2026-000042. Required by the accountants."""
import re

from shopkit.config import SETTINGS

_PATTERN = re.compile(r"^(?P<prefix>[A-Z]+)-(?P<year>\d{4})-(?P<seq>\d{6})$")


def next_invoice_number(last: str | None, year: int, prefix: str = SETTINGS.invoice_prefix) -> str:
    """The number after `last`. Numbering restarts at 000001 when the year changes or there is no
    previous invoice. Raises ValueError if `last` isn't in PREFIX-YYYY-NNNNNN form."""
    if last is None:
        return f"{prefix}-{year}-000001"
    m = _PATTERN.match(last)
    if not m:
        raise ValueError(f"bad invoice number {last!r}")
    seq = int(m["seq"]) + 1 if int(m["year"]) == year else 1
    return f"{prefix}-{year}-{seq:06d}"


def invoice_year(number: str) -> int:
    m = _PATTERN.match(number)
    if not m:
        raise ValueError(f"bad invoice number {number!r}")
    return int(m["year"])
