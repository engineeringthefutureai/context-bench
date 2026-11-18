_SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


def format_money(cents: int, currency: str = "USD") -> str:
    """Human-readable amount as shown to customers, e.g. 123456 -> "$1,234.56"."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    symbol = _SYMBOLS.get(currency, currency + " ")
    return f"{sign}{symbol}{cents // 100:,}.{cents % 100:02d}"


def parse_money(text: str) -> int:
    """Inverse of format_money for USD-style strings; returns cents."""
    clean = text.replace(",", "").replace("$", "").strip()
    negative = clean.startswith("-")
    whole, _, frac = clean.lstrip("-").partition(".")
    cents = int(whole or 0) * 100 + int((frac + "00")[:2])
    return -cents if negative else cents
