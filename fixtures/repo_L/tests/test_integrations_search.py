import json
from datetime import date

import pytest

from shopkit.catalog.models import Product
from shopkit.core.erp import ErpSync
from shopkit.customers.models import Address
from shopkit.integrations.carriers_api import CarrierClient, CarrierError
from shopkit.integrations.erp_client import ErpClient, PurchaseOrder
from shopkit.integrations.tax_provider import FAILURE_BACKOFF_S, TaxProvider
from shopkit.invoices.issuing import IssuedInvoice
from shopkit.orders.models import Order, OrderLine
from shopkit.search.index import SearchIndex, tokens


def test_erp_client_invoice_and_purchase_order():
    bodies = []
    client = ErpClient(ErpSync(lambda body, h: bodies.append(body) or "<Reply status='OK' ref='R'/>", sleep=lambda s: None),
                       customer_numbers={"C1": "K1001"})
    inv = IssuedInvoice("INV-2026-0001", "O1", date(2026, 3, 1), "")
    ok, ref = client.push_invoice(inv, Order("O1", [OrderLine("MUG-1", 2, 1250)]), customer_id="C1",
                                  tax_cents_by_sku={"MUG-1": 200})
    assert ok and "<CustNo>K1001</CustNo>" in bodies[0] and "<Item>MUG000000001</Item>" in bodies[0]
    po = PurchaseOrder("PO-1", "ACME", [("TEA-9", 12, 300)], date(2026, 3, 20))
    assert "<Item>TEA000000009</Item><Qty>12</Qty><Cost>3.00</Cost>" in client.purchase_order_xml(po)
    assert client.send_purchase_order(po) == (True, "R") and client.send_purchase_order(po) == (True, "dup")


def test_tax_provider_cache_and_fallback():
    t = [1000.0]
    calls = []
    def transport(*key):
        calls.append(key)
        if len(calls) == 2:
            raise TimeoutError
        return 8.875
    tp = TaxProvider(transport, clock=lambda: t[0])
    assert tp.rate("US", "NY", "10001-1234") == 8.875 and tp.rate("US", "NY", "10001") == 8.875 and len(calls) == 1
    assert tp.rate("US", "CA", "94110") == 7.25 and tp.fallbacks == 1          # provider failed: static table
    assert tp.rate("US", "TX", "73301") == 6.25 and len(calls) == 2            # backing off, no call
    t[0] += FAILURE_BACKOFF_S + 1
    assert tp.rate("US", "TX", "73301") == 8.875 and tp.rate("DE") == 19.0


def test_carrier_labels_and_tracking():
    def transport(path, body):
        if path == "/labels":
            return 201, json.dumps({"tracking_number": "1Z9", "label": "UERG"})
        if path == "/tracking/1Z9":
            return 200, json.dumps({"events": [{"time": "2026-03-02T09:00", "code": "I"}, {"time": "2026-03-01T17:00", "code": "P"},
                                               {"time": "2026-03-03T10:00", "code": "ZZ"}]})
        return 404, ""
    c = CarrierClient("ups", transport)
    label = c.create_label("SH1", Address("Ann", "1 Main", "X", "1", "US"), 500)
    assert label.tracking_number == "1Z9"
    assert [s for _, s in c.tracking("1Z9")] == ["picked_up", "in_transit", "in_transit"] and c.tracking("nope") == []
    with pytest.raises(CarrierError):
        CarrierClient("ups", lambda p, b: (500, "down")).create_label("S", Address("A", "1", "X", "1", "US"), 1)


def test_search_ranking_synonyms_prefix_and_suggest():
    assert tokens("The Café Mugs & Glasses") == ["cafe", "mug", "glasse"]
    idx = SearchIndex()
    idx.add(Product("MUG-1", "Blue mug", 1000), category="Kitchen", popularity=50)
    idx.add(Product("CUP-2", "Espresso cup", 800), category="Kitchen")
    idx.add(Product("TEE-3", "Blue tee", 2000), category="Clothing", tags=["summer"])
    idx.add(Product("OLD-4", "Blue mug old", 900, active=False))
    assert [s for s, _ in idx.search("blue mu")] == ["MUG-1"]
    assert {s for s, _ in idx.search("cup")} == {"MUG-1", "CUP-2"}
    assert idx.search("mug")[0][0] == "MUG-1" and idx.search("tshirt")[0][0] == "TEE-3"
    assert "OLD-4" in [s for s, _ in idx.search("old", include_inactive=True)] and idx.search("old") == []
    assert idx.suggest("bl") == ["blue"]
    idx.remove("TEE-3")
    assert idx.search("tee") == []
