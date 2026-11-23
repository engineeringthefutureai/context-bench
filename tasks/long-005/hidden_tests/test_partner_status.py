from shopkit.api.partner_status import explain_delay


def test_events_for_orders_in_review_are_held():
    assert explain_delay("order.paid", "O1", lambda oid: True)
    assert explain_delay("order.paid", "O1", lambda oid: False) is None


def test_cancellations_and_refunds_are_never_held():
    assert explain_delay("order.cancelled", "O1", lambda oid: True) is None
    assert explain_delay("refund.settled", "O1", lambda oid: True) is None


def test_failed_review_lookup_holds_and_events_without_order_go_out():
    def broken(oid):
        raise RuntimeError("fraud service down")
    assert explain_delay("order.shipped", "O1", broken)
    assert explain_delay("product.updated", None, lambda oid: True) is None
