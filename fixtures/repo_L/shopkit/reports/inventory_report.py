"""Stock health report for purchasing."""
from shopkit.inventory.reorder import Suggestion
from shopkit.reports.formatting import fmt_count, pad_table


def reorder_report(suggestions: list[Suggestion]) -> str:
    rows = [["sku", "on hand", "cover (days)", "order"]]
    for s in suggestions:
        cover = "∞" if s.cover_days == float("inf") else f"{s.cover_days:.1f}"
        rows.append([s.sku, fmt_count(s.on_hand), cover, fmt_count(s.order_qty)])
    return pad_table(rows)
