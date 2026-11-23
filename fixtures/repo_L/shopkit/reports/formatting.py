"""Number formatting for internal reports (CSV/spreadsheet friendly, not customer-facing)."""


def fmt_amount(cents: int) -> str:
    """Plain decimal amount for spreadsheets: no currency symbol, no thousands separator.
    fmt_amount(123456) -> "1234.56"."""
    sign = "-" if cents < 0 else ""
    cents = abs(cents)
    return f"{sign}{cents // 100}.{cents % 100:02d}"


def fmt_percent(ratio: float, digits: int = 1) -> str:
    return f"{ratio * 100:.{digits}f}%"


def fmt_count(n: int) -> str:
    return f"{n:,}"


def pad_table(rows: list[list[str]]) -> str:
    """Left-aligned fixed-width text table; first row is the header."""
    if not rows:
        return ""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = ["  ".join(cell.ljust(w) for cell, w in zip(row, widths)).rstrip() for row in rows]
    lines.insert(1, "  ".join("-" * w for w in widths))
    return "\n".join(lines)
