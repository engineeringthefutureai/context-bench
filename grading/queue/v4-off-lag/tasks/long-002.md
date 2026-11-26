Support agents want to tell a customer, before pressing Refund, where the money will go back to.
Add `refund_destinations(order_id, amount_cents, payments, today)` in a new module
`shopkit/support/refund_info.py`. It returns a dict with the cents that would go back to the card,
to store credit and to a gift card (keys "card", "store_credit", "gift_card"), split exactly the
way a refund of that amount for that order would be settled. It must not refund anything.
