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
