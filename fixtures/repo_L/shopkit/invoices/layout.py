"""Invoice header and footer blocks shared by the text and HTML invoice renderers."""
from datetime import date

from shopkit.config import SETTINGS
from shopkit.customers.addresses import format_address
from shopkit.customers.models import Address
from shopkit.utils.dates import add_business_days

PAYMENT_TERMS_DAYS = 14
COMPANY = ["Shopkit Ltd", "1 Market Street", "Springfield, IL 62701", "US"]


def header(invoice_number: str, issued: date, bill_to: Address) -> str:
    due = add_business_days(issued, PAYMENT_TERMS_DAYS)
    parts = ["\n".join(COMPANY), "", f"Invoice number: {invoice_number}",
             f"Issued: {issued.isoformat()}   Due: {due.isoformat()}", "", "Bill to:",
             format_address(bill_to)]
    return "\n".join(parts)


def footer() -> str:
    return f"Questions? Contact {SETTINGS.support_email}. Thank you for your order."
