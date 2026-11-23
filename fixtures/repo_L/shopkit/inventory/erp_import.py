"""Morning stock import: the ERP's stock file sets available quantities per warehouse.

Parsing is done by shopkit.core.erp (the file format is shared with finance tooling); this module
applies the result to the stock records and reports what changed."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from shopkit.events import BUS, EventBus
from shopkit.inventory.stock import Stock
from shopkit.core.erp import parseStockFile

log = logging.getLogger(__name__)


@dataclass
class ImportResult:
    changed: dict[tuple[str, str], tuple[int, int]] = field(default_factory=dict)   # (wh, sku) -> (old, new)
    warnings: list[str] = field(default_factory=list)


def apply_stock_file(text: str, stock_by_warehouse: dict[str, Stock], known_skus: list[str],
                     bus: EventBus = BUS) -> ImportResult:
    """Set each warehouse's available stock from the file. SKUs in the file overwrite; SKUs missing
    from the file are left alone (the ERP only sends items with movement)."""
    levels, warnings = parseStockFile(text, known_skus)
    result = ImportResult(warnings=list(warnings))
    for (wh, sku), qty in levels.items():
        stock = stock_by_warehouse.setdefault(wh, Stock())
        old = stock.available(sku)
        if old != qty:
            stock.receive(sku, qty - old)          # Stock has no setter; receive() takes the difference
            result.changed[(wh, sku)] = (old, qty)
    if result.changed:
        bus.publish("stock.imported", changed=len(result.changed), warnings=len(result.warnings))
    for w in result.warnings:
        log.warning("stock file: %s", w)
    return result
