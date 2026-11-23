"""Short helpers for scripts and notebooks."""
from shopkit.money import format_money, parse_money
from shopkit.utils.dates import add_business_days
from shopkit.utils.text import normalize_sku


def fmt_price(cents, currency="USD"):
    return format_money(cents, currency)


def price_from_text(text):
    return parse_money(text)


def clean_sku(raw):
    return normalize_sku(raw)


def business_days_after(start, days):
    return add_business_days(start, days)
