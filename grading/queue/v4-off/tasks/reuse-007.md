Ops wants a weekly view of stock rebalancing. Add `transfers_by_week(transfers) -> dict[str, int]`
to the transfers module: total units moved per week, keyed by week with the same week labels the
other dashboards use, in chronological order.
