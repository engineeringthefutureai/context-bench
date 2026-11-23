"""Bulk product import from the merchandising team's CSV sheet."""
from dataclasses import dataclass, field

from shopkit.catalog.models import Product
from shopkit.money import parse_money
from shopkit.utils.csvio import read_csv
from shopkit.utils.text import normalize_sku

REQUIRED_COLUMNS = ("sku", "title", "price")


@dataclass
class ImportReport:
    created: list[Product] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)       # "row 3: missing title"

    @property
    def ok(self) -> bool:
        return not self.errors


def import_products(csv_text: str, existing_skus: set[str] | None = None) -> ImportReport:
    """Parse rows into Products. Bad rows are reported, not raised; the rest are still imported.
    Duplicate SKUs (within the file or against `existing_skus`) are errors."""
    report = ImportReport()
    seen = {normalize_sku(s) for s in (existing_skus or set())}
    rows = read_csv(csv_text)
    if rows and (missing := [c for c in REQUIRED_COLUMNS if c not in rows[0]]):
        report.errors.append(f"missing columns: {', '.join(missing)}")
        return report
    for n, row in enumerate(rows, start=2):           # row 1 is the header
        if not row["title"]:
            report.errors.append(f"row {n}: missing title")
            continue
        sku = normalize_sku(row["sku"])
        if not sku:
            report.errors.append(f"row {n}: missing sku")
            continue
        if sku in seen:
            report.errors.append(f"row {n}: duplicate sku {sku}")
            continue
        try:
            price = parse_money(row["price"])
        except ValueError:
            report.errors.append(f"row {n}: bad price {row['price']!r}")
            continue
        active = row.get("active", "yes").lower() not in ("no", "false", "0")
        seen.add(sku)
        report.created.append(Product(sku=sku, title=row["title"], price_cents=price, active=active))
    return report


# ---- supplier feeds and updates (2025) ------------------------------------------------------------
# Suppliers send JSON feeds; merchandising still uses the CSV sheet. Both end up here. Updates to
# existing products only touch price and active; titles are owned by merchandising and never
# overwritten by a feed.
import json
from collections.abc import Callable

MAX_PRICE_CHANGE_PCT = 50        # a bigger jump in one feed is held for review (the "1.99 instead of 19.99" case)


@dataclass
class UpdateReport:
    updated: list[str] = field(default_factory=list)
    held: list[tuple[str, int, int]] = field(default_factory=list)       # (sku, old cents, new cents)
    unknown: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def parse_feed(json_text: str) -> list[dict]:
    """Supplier JSON feed: {"supplier": "...", "items": [{"sku", "price_cents", "available"}]}."""
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as e:
        raise ValueError(f"feed is not valid JSON: {e.msg} at line {e.lineno}") from None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("feed has no items list")
    return items


def apply_updates(items: list[dict], products: dict[str, Product], *,
                  dry_run: bool = False, on_hold: Callable[[str, int, int], None] | None = None) -> UpdateReport:
    """Apply price/availability updates from a feed to known products (keyed by normalized SKU).
    Price changes over MAX_PRICE_CHANGE_PCT are held, not applied. dry_run reports without changing."""
    report = UpdateReport()
    for n, item in enumerate(items, start=1):
        try:
            sku = normalize_sku(str(item["sku"]))
            new_price = int(item["price_cents"])
        except (KeyError, TypeError, ValueError):
            report.errors.append(f"item {n}: needs sku and integer price_cents")
            continue
        product = products.get(sku)
        if product is None:
            report.unknown.append(sku)
            continue
        if new_price < 0:
            report.errors.append(f"item {n}: negative price for {sku}")
            continue
        old = product.price_cents
        if old and abs(new_price - old) * 100 > old * MAX_PRICE_CHANGE_PCT:
            report.held.append((sku, old, new_price))
            if on_hold and not dry_run:
                on_hold(sku, old, new_price)
            continue
        if not dry_run:
            product.price_cents = new_price
            if "available" in item:
                product.active = bool(item["available"])
        report.updated.append(sku)
    return report


def diff_sheet(csv_text: str, products: dict[str, Product]) -> dict[str, list[str]]:
    """What importing a sheet would change, for the review screen: new SKUs, price changes, titles
    that differ (shown, not applied)."""
    out: dict[str, list[str]] = {"new": [], "price": [], "title": []}
    for row in read_csv(csv_text):
        sku = normalize_sku(row.get("sku", ""))
        if not sku:
            continue
        current = products.get(sku)
        if current is None:
            out["new"].append(sku)
            continue
        try:
            if parse_money(row.get("price", "")) != current.price_cents:
                out["price"].append(sku)
        except ValueError:
            pass
        if row.get("title") and row["title"] != current.title:
            out["title"].append(sku)
    return out
