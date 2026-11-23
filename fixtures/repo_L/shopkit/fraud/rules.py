"""Risk signals for an order. Each rule returns points (0 = no signal) and a reason code shown to the
reviewer. Scores add up; thresholds in scoring.py decide accept / review / reject."""
from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from datetime import datetime, timedelta

HIGH_RISK_COUNTRIES = {"NG", "GH", "ID", "VN"}      # chargeback rate > 3% in the last 12 months (risk team list)
DISPOSABLE_DOMAINS = {"mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.dev", "yopmail.com"}
GIFT_CARD_SKU_PREFIX = "GC"


@dataclass
class OrderFacts:
    order_id: str
    customer_id: str | None
    email: str
    total_cents: int
    currency: str
    billing_country: str
    shipping_country: str
    ip: str = ""
    ip_country: str = ""
    skus: list[str] = field(default_factory=list)
    card_bin: str = ""
    card_country: str = ""
    account_age_days: int | None = None
    previous_orders: int = 0
    previous_chargebacks: int = 0
    placed_at: datetime | None = None
    recent_orders_same_card: int = 0            # last 24 h, any customer
    recent_failed_payments: int = 0             # last hour, this customer or card


@dataclass(frozen=True)
class Signal:
    code: str
    points: int
    detail: str = ""


def country_mismatch(f: OrderFacts) -> Signal | None:
    if f.billing_country and f.shipping_country and f.billing_country != f.shipping_country:
        return Signal("country_mismatch", 15, f"bill {f.billing_country} / ship {f.shipping_country}")
    return None


def card_country_mismatch(f: OrderFacts) -> Signal | None:
    if f.card_country and f.billing_country and f.card_country != f.billing_country:
        return Signal("card_country", 20, f"card issued in {f.card_country}")
    return None


def ip_mismatch(f: OrderFacts) -> Signal | None:
    if not f.ip:
        return None
    try:
        addr = ipaddress.ip_address(f.ip)
    except ValueError:
        return Signal("bad_ip", 10, f.ip)
    if addr.is_private or addr.is_loopback:
        return None                                  # internal tools, phone orders
    if f.ip_country and f.ip_country not in (f.billing_country, f.shipping_country):
        return Signal("ip_country", 15, f"ip in {f.ip_country}")
    return None


def high_risk_destination(f: OrderFacts) -> Signal | None:
    if f.shipping_country in HIGH_RISK_COUNTRIES:
        return Signal("high_risk_country", 25, f.shipping_country)
    return None


def disposable_email(f: OrderFacts) -> Signal | None:
    domain = f.email.rsplit("@", 1)[-1].lower() if "@" in f.email else ""
    if domain in DISPOSABLE_DOMAINS:
        return Signal("disposable_email", 25, domain)
    return None


def new_account_big_order(f: OrderFacts) -> Signal | None:
    if f.account_age_days is not None and f.account_age_days < 2 and f.total_cents >= 50_000:
        return Signal("new_account_big_order", 30, f"{f.account_age_days} days old")
    return None


def gift_cards(f: OrderFacts) -> Signal | None:
    count = sum(1 for s in f.skus if s.upper().startswith(GIFT_CARD_SKU_PREFIX))
    if count and f.previous_orders == 0:
        return Signal("gift_cards_first_order", 20 + 5 * min(count - 1, 4), f"{count} gift cards")
    return None


def card_velocity(f: OrderFacts) -> Signal | None:
    if f.recent_orders_same_card >= 4:
        return Signal("card_velocity", 35, f"{f.recent_orders_same_card} orders on this card in 24 h")
    return None


def failed_payments(f: OrderFacts) -> Signal | None:
    if f.recent_failed_payments >= 3:
        return Signal("failed_payments", 20 + 5 * min(f.recent_failed_payments - 3, 4), f"{f.recent_failed_payments} declines in 1 h")
    return None


def chargeback_history(f: OrderFacts) -> Signal | None:
    if f.previous_chargebacks:
        return Signal("chargeback_history", 40 * min(f.previous_chargebacks, 2), f"{f.previous_chargebacks} chargebacks")
    return None


def odd_hours(f: OrderFacts) -> Signal | None:
    if f.placed_at and 2 <= f.placed_at.hour < 5 and f.total_cents >= 20_000:
        return Signal("odd_hours", 5, f.placed_at.strftime("%H:%M"))
    return None


def trusted_customer(f: OrderFacts) -> Signal | None:
    """Negative points: a long-standing customer without chargebacks."""
    if f.previous_orders >= 5 and not f.previous_chargebacks and (f.account_age_days or 0) >= 180:
        return Signal("trusted_customer", -30, f"{f.previous_orders} previous orders")
    return None


RULES = [country_mismatch, card_country_mismatch, ip_mismatch, high_risk_destination, disposable_email,
         new_account_big_order, gift_cards, card_velocity, failed_payments, chargeback_history, odd_hours,
         trusted_customer]


def evaluate(facts: OrderFacts, rules=None) -> list[Signal]:
    out = []
    for rule in rules or RULES:
        sig = rule(facts)
        if sig is not None:
            out.append(sig)
    return out
