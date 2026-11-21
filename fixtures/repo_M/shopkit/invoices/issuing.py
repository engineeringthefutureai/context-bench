"""Issue invoices for paid orders and credit notes for refunds, keeping both sequences gap-free."""
from dataclasses import dataclass, field
from datetime import date

from shopkit.customers.models import Address
from shopkit.invoices import credit_notes
from shopkit.invoices.layout import footer, header
from shopkit.invoices.numbering import next_invoice_number
from shopkit.invoices.render import render_invoice_text
from shopkit.orders.models import Order


@dataclass
class IssuedInvoice:
    number: str
    order_id: str
    issued: date
    text: str


@dataclass
class InvoiceRegister:
    invoices: list[IssuedInvoice] = field(default_factory=list)
    credit_notes: list[credit_notes.CreditNote] = field(default_factory=list)

    def last_number(self) -> str | None:
        return self.invoices[-1].number if self.invoices else None

    def issue(self, order: Order, bill_to: Address, issued: date) -> IssuedInvoice:
        if any(i.order_id == order.id for i in self.invoices):
            raise ValueError(f"order {order.id} is already invoiced")
        number = next_invoice_number(self.last_number(), issued.year)
        text = "\n\n".join([header(number, issued, bill_to), render_invoice_text(order), footer()])
        invoice = IssuedInvoice(number, order.id, issued, text)
        self.invoices.append(invoice)
        return invoice

    def credit(self, order_id: str, refund_cents: int, currency: str, issued: date,
               reason: str) -> credit_notes.CreditNote:
        invoice = next(i for i in self.invoices if i.order_id == order_id)
        last = self.credit_notes[-1].number if self.credit_notes else None
        note = credit_notes.issue(last, invoice.number, refund_cents, currency, issued, reason)
        self.credit_notes.append(note)
        return note
