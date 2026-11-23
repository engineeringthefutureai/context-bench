from shopkit.payments.models import CAPTURED, Payment
from shopkit.payments.reconciliation import Mismatch, reconcile, redact_pan


def test_redact_pan():
    assert redact_pan("4242 4242 4242 4242") == "************4242"
    assert redact_pan("123") == "***"


def test_reconcile():
    p1 = Payment("p1", "o1", 1000, "USD", "card", status=CAPTURED)
    p2 = Payment("p2", "o2", 500, "USD", "card", status=CAPTURED, refunded_cents=100)
    csv = "payment_id,amount_cents\np1,1000\np2,500\np3,70\n"
    assert reconcile([p1, p2], csv) == [Mismatch("p2", 400, 500), Mismatch("p3", None, 70)]
