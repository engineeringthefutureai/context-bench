from dataclasses import dataclass

import pytest

from shopkit.catalog.models import Product
from shopkit.customers.models import Address, Customer
from shopkit.orders.models import Order, OrderLine
from shopkit.web import views
from shopkit.web.forms import AddressForm, ContactForm, RefundForm, field_views
from shopkit.web.templating import Environment, TemplateError, TemplateNotFound, evaluate, lookup


@pytest.fixture
def env():
    return Environment()


def test_variables_are_escaped_and_raw_is_not(env):
    t = env.from_string("<p>{{ name }}</p>{{ html|raw }}")
    assert t.render(name="<b>Bo & Co</b>", html="<i>ok</i>") == "<p>&lt;b&gt;Bo &amp; Co&lt;/b&gt;</p><i>ok</i>"


def test_lookup_keys_attributes_indexes_and_literals():
    @dataclass
    class O:
        id: str
    ctx = {"o": O("A1"), "d": {"k": [10, 20]}}
    assert lookup(ctx, "o.id") == "A1" and lookup(ctx, "d.k.1") == 20 and lookup(ctx, '"x"') == "x"
    assert str(lookup(ctx, "o.nope.deeper")) == "" and not lookup(ctx, "missing")


def test_conditions():
    ctx = {"n": 3, "s": "paid", "items": ["a"]}
    assert evaluate(ctx, "n > 2 and s == 'paid'") and evaluate(ctx, "'a' in items")
    assert evaluate(ctx, "not missing") and not evaluate(ctx, "n < 2 or s != 'paid'")


def test_if_for_empty_loop_and_filters(env):
    t = env.from_string("{% for x in xs %}{{ loop.index }}:{{ x|upper }}{% if not loop.last %},{% endif %}"
                        "{% empty %}none{% endfor %}|{% if n == 1 %}one{% elif n == 2 %}two{% else %}many{% endif %}")
    assert t.render(xs=["a", "b"], n=2) == "1:A,2:B|two"
    assert t.render(xs=[], n=5) == "none|many"
    assert env.from_string("{{ c|money:'EUR' }} {{ t|truncate:5 }}").render(c=123456, t="abcdefgh") == "€1,234.56 abcd…"


def test_unknown_filter_tag_and_template(env):
    with pytest.raises(TemplateError):
        env.from_string("{{ x|nope }}").render(x=1)
    with pytest.raises(TemplateError):
        env.from_string("{% frobnicate %}")
    with pytest.raises(TemplateNotFound):
        env.get("../secrets.html")


def _order():
    return Order("O-1001", [OrderLine("MUG-1", 2, 1250), OrderLine("TEA-9", 1, 899)], currency="USD")


def test_order_page_shows_lines_totals_status_and_history(env):
    html = views.order_detail(_order(), status="shipped", env=env,
                              products={"MUG-1": Product("MUG-1", "Blue mug", 1250)},
                              history=[("paid", "2026-03-01T10:00:00", "api"), ("shipped", "2026-03-02T09:30:00", "wms")],
                              tracking=[{"carrier": "ups", "number": "1Z999", "url": "https://ups.example/1Z999"}])
    assert "<h1>Order O-1001</h1>" in html and "Blue mug" in html and "TEA-9" in html
    assert "$33.99" in html and "On its way" in html and "02 Mar 2026 09:30" in html and "UPS 1Z999" in html
    assert "by wms" not in html                       # actors only on admin pages
    admin = views.order_detail(_order(), status="cancelled", history=[("cancelled", "2026-03-03T00:00:00", "ann")],
                               admin=True, env=env)
    assert "by ann" in admin and "(closed)" in admin and "Cancel order" not in admin


def test_order_list_pagination(env):
    @dataclass
    class Row:
        id: str
        status: str
        total_cents: int
        currency: str
        created_at: str
        channel: str = "web"
    rows = [Row(f"O{i}", "paid", 1000, "USD", "2026-03-01T00:00:00") for i in range(2)]
    html = views.order_list(rows, total=5, offset=2, limit=2, env=env)
    assert 'href="?offset=0"' in html and 'href="?offset=4"' in html and "5 orders" in html


def test_product_page_price_list_and_stock(env):
    p = Product("MUG-1", "Blue mug", 1500)
    html = views.product_page(p, unit_cents=1200, in_stock=3, env=env)
    assert "$12.00" in html and "<s class=\"was\">$15.00</s>" in html and "Only 3 left" in html
    assert "Currently unavailable" in views.product_page(p, in_stock=0, env=env)


def test_account_and_invoice_and_error_pages(env):
    c = Customer("C1", "ann@example.com", "Ann Lee", tier="gold",
                 addresses=[Address("Ann Lee", "1 Main St", "Springfield", "12345", "US")])
    html = views.account_page(c, points=120, env=env)
    assert "Hello, Ann" in html and "Gold member · 120 points" in html and "1 Main St" in html
    inv = views.invoice_page("INV-2026-0001", _order(), bill_to="Ann Lee", issued="2026-03-01", env=env)
    assert "Invoice INV-2026-0001" in inv and "$33.99" in inv
    assert "Not found" in views.error_page(404, "No such order", env=env)


def test_forms_validate_and_report_errors():
    good = AddressForm({"name": "Ann", "line1": "1 Main", "city": "X", "postcode": "123", "country": "US"})
    assert good.is_valid() and good.cleaned["line2"] == ""
    bad = AddressForm({"name": "", "country": "ZZ"})
    assert not bad.is_valid() and bad.errors["name"] == "This field is required."
    assert bad.errors["country"] == "Choose one of the listed options."
    contact = ContactForm({"email": "Ann@Example.com", "subject": "Hi", "message": "x"})
    assert contact.is_valid() and contact.cleaned["email"] == "ann@example.com"
    refund = RefundForm({"amount": "30.00", "reason": "damaged"}, max_refundable_cents=2500)
    assert not refund.is_valid() and "more than" in refund.errors["__all__"]
    assert [f.name for f in field_views(bad)][:2] == ["name", "line1"]
