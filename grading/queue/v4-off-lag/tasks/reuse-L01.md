We're opening pop-up stores in Canada with cash tills. Add `amount_due(total_cents, currency,
paid_by)` in a new module `shopkit/pos/till.py` (a new package): the amount the cashier asks the
customer to pay. `paid_by` is "card" or "cash". Card payments pay the exact total; cash payments
follow the national rules for cash amounts, the same way the shop already treats cash elsewhere.
