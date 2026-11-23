"""Template filters. Money and dates are formatted the way the rest of the shop does it, so a page
never shows an amount differently from the invoice or the email."""
from __future__ import annotations

from datetime import date, datetime

from shopkit.money import format_money


def money(cents, currency="USD") -> str:
    if cents is None or cents == "":
        return ""
    return format_money(int(cents), currency or "USD")


def datefmt(value, fmt="%d %b %Y") -> str:
    if not value:
        return ""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, (date, datetime)):
        return value.strftime(fmt)
    return str(value)


def truncate(value, length=40) -> str:
    text = "" if value is None else str(value)
    length = int(length)
    return text if len(text) <= length else text[: max(length - 1, 0)].rstrip() + "…"


def default(value, fallback="") -> object:
    return value if value not in (None, "") and bool(str(value)) else fallback


def pluralize(count, word="item") -> str:
    n = int(count or 0)
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def title(value) -> str:
    return str(value or "").replace("_", " ").title()


def length(value) -> int:
    try:
        return len(value)
    except TypeError:
        return 0


def join(value, sep=", ") -> str:
    return sep.join(str(v) for v in (value or []))


def yesno(value, choices="yes,no") -> str:
    yes, _, no = str(choices).partition(",")
    return yes if value else no


def percent(value, digits=0) -> str:
    """0.125 -> "12.5%" with digits=1; basis points are converted by the caller."""
    if value in (None, ""):
        return ""
    return f"{float(value) * 100:.{int(digits)}f}%"


DEFAULT_FILTERS = {
    "money": money, "date": datefmt, "truncate": truncate, "default": default, "pluralize": pluralize,
    "title": title, "length": length, "join": join, "yesno": yesno, "percent": percent,
    "upper": lambda v: str(v or "").upper(), "lower": lambda v: str(v or "").lower(),
}
