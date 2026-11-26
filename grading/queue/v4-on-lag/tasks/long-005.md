Partners keep asking why some order events reach them late. Add `explain_delay(event, order_id,
under_review)` in a new module `shopkit/api/partner_status.py`. `under_review` is a callable that
tells whether an order is in fraud review. Return a short reason (any non-empty string) when our
webhook worker would hold that event back right now, and None when it would send it normally.
