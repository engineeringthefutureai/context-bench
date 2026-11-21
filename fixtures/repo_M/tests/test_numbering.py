import pytest

from shopkit.invoices.numbering import invoice_year, next_invoice_number


def test_next_invoice_number():
    assert next_invoice_number(None, 2026) == "INV-2026-000001"
    assert next_invoice_number("INV-2026-000041", 2026) == "INV-2026-000042"
    assert next_invoice_number("INV-2025-000900", 2026) == "INV-2026-000001"
    assert next_invoice_number(None, 2026, prefix="CN") == "CN-2026-000001"


def test_bad_numbers():
    with pytest.raises(ValueError):
        next_invoice_number("INV-26-1", 2026)
    assert invoice_year("INV-2027-000003") == 2027
