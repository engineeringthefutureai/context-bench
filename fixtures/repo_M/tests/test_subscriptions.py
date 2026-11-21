from datetime import date

from shopkit.orders.subscriptions import Subscription, generate_order, run_due

PRICES = {"BEANS": 1500, "FILTER": 399}


def test_generate_order_applies_discount_per_unit():
    sub = Subscription("s1", "c1", {"BEANS": 2, "FILTER": 1}, "monthly", date(2026, 11, 1))
    order = generate_order(sub, PRICES)
    assert [(l.sku, l.qty, l.unit_cents) for l in order.lines] == [("BEANS", 2, 1350), ("FILTER", 1, 359)]


def test_run_due_advances_and_respects_pause():
    due = Subscription("s1", "c1", {"BEANS": 1}, "weekly", date(2026, 11, 20))
    paused = Subscription("s2", "c2", {"BEANS": 1}, "weekly", date(2026, 11, 1), paused=True)
    orders = run_due([due, paused], PRICES, date(2026, 11, 20))
    assert len(orders) == 1 and due.next_run == date(2026, 11, 27)


def test_skip_next():
    sub = Subscription("s1", "c1", {"BEANS": 1}, "fortnightly", date(2026, 12, 1))
    assert sub.skip_next() == date(2026, 12, 1) and sub.next_run == date(2026, 12, 15)
