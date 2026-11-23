"""ERP client for the current services: invoices, stock and purchase orders over the ERP's XML gateway.
Item numbers and message formats come from shopkit.core.erp, which the finance tooling shares."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from shopkit.core.erp import ErpSync, erpAmount, toErpSku
from shopkit.invoices.issuing import IssuedInvoice
from shopkit.orders.models import Order

log = logging.getLogger(__name__)


@dataclass
class PurchaseOrder:
    number: str
    supplier: str
    lines: list[tuple[str, int, int]]          # (sku, qty, unit cost cents)
    expected: date | None = None


@dataclass
class ErpClient:
    sync: ErpSync
    customer_numbers: dict[str, str] = field(default_factory=dict)   # shop customer id -> ERP customer number
    sent_pos: set[str] = field(default_factory=set)

    def push_invoice(self, invoice: IssuedInvoice, order: Order, *, tax_cents_by_sku: dict[str, int] | None = None,
                     shipping_cents: int = 0, customer_id: str | None = None) -> tuple[bool, str]:
        """Send an issued invoice. Tax per line comes from the invoice calculation; the ERP wants it per line."""
        tax = tax_cents_by_sku or {}
        payload = {"number": invoice.number, "date": invoice.issued, "currency": order.currency,
                   "customerNo": self.customer_numbers.get(customer_id or "", "WEBSHOP"),
                   "lines": [{"sku": l.sku, "qty": l.qty, "unitCents": l.unit_cents, "taxCents": tax.get(l.sku, 0)}
                             for l in order.lines],
                   "shippingCents": shipping_cents}
        ok, ref = self.sync.pushInvoice(payload)
        if not ok:
            log.warning("ERP rejected invoice %s: %s", invoice.number, ref)
        return ok, ref

    def purchase_order_xml(self, po: PurchaseOrder) -> str:
        """The ERP's PURCHASE message (built by hand: core.erp only has invoices)."""
        rows = [f'<Message type="PURCHASE" company="SHOP01" version="2">',
                f"<Header><DocNo>{po.number}</DocNo><Supplier>{po.supplier}</Supplier>"
                + (f"<Expected>{po.expected:%Y%m%d}</Expected>" if po.expected else "") + "</Header>", "<Lines>"]
        for i, (sku, qty, cost) in enumerate(po.lines, 1):
            rows.append(f'<Line no="{i}"><Item>{toErpSku(sku)}</Item><Qty>{qty}</Qty><Cost>{erpAmount(cost)}</Cost></Line>')
        rows += ["</Lines>", "</Message>"]
        return "".join(rows)

    def send_purchase_order(self, po: PurchaseOrder) -> tuple[bool, str]:
        if po.number in self.sent_pos:
            return True, "dup"
        if not po.lines:
            return False, "empty purchase order"
        ok, ref = self.sync._post(self.purchase_order_xml(po))
        if ok:
            self.sent_pos.add(po.number)
        return ok, ref
