from datetime import datetime, timedelta

import pytest

from shopkit.events import EventBus
from shopkit.support.routing import render_macro, route
from shopkit.support.sla import add_business_minutes, breached, business_minutes, first_response_due
from shopkit.support.tickets import CLOSED, OPEN, PENDING_CUSTOMER, SOLVED, Desk, TicketClosed

MON = datetime(2026, 3, 9, 9, 0)


def test_ticket_flow_threads_and_reopens():
    desk = Desk(EventBus())
    t = desk.open("Where is ord_0123456789ab?", "Still not here", MON, customer_id="C1")
    assert t.order_id == "ord_0123456789ab"
    desk.reply(t.id, "ann", "Checking", MON + timedelta(hours=1))
    assert t.status == PENDING_CUSTOMER and t.first_response_at == MON + timedelta(hours=1)
    desk.customer_email(t.id, "Re", "Thanks", MON + timedelta(hours=2))
    assert t.status == OPEN
    desk.reply(t.id, "ann", "Sent again", MON + timedelta(hours=3), solve=True)
    late = desk.customer_email(t.id, "Re", "Broken now", MON + timedelta(days=10))
    assert late.id != t.id and f"follow_up:{t.id}" in late.tags
    assert desk.close_solved(MON + timedelta(days=5)) == [t.id]
    with pytest.raises(TicketClosed):
        desk.reply(t.id, "ann", "x", MON + timedelta(days=6))


def test_business_minutes_skip_nights_and_weekends():
    fri_5pm = datetime(2026, 3, 13, 17, 0)
    assert business_minutes(fri_5pm, datetime(2026, 3, 16, 9, 0)) == 120
    assert add_business_minutes(fri_5pm, 90) == datetime(2026, 3, 16, 8, 30)
    assert add_business_minutes(datetime(2026, 3, 14, 12), 10) == datetime(2026, 3, 16, 8, 10)


def test_sla_breaches_ignore_time_waiting_for_customer():
    desk = Desk(EventBus())
    t = desk.open("Help", "x", MON)
    t.priority = "urgent"
    assert first_response_due(t) == MON + timedelta(hours=1)
    assert breached(t, MON + timedelta(hours=2)) == ["first_response"]
    desk.reply(t.id, "ann", "need info", MON + timedelta(minutes=30))
    assert breached(t, MON + timedelta(days=3)) == []


def test_routing_and_macros():
    desk = Desk(EventBus())
    t = route(desk.open("Charged twice", "I want my money back or I call my lawyer", MON))
    assert t.queue == "billing" and t.priority == "urgent" and "escalation" in t.tags
    t2 = route(desk.open("Hello", "tracking please", MON), tier="gold")
    assert t2.queue == "shipping" and t2.priority == "high"
    body, macro = render_macro("refund_done", first_name="", order_id="O1", agent="Ann")
    assert body.startswith("Hi there") and "O1" in body and macro.solves
