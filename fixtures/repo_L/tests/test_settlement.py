from datetime import date, datetime

import pytest

from shopkit.accounting import accounts as A
from shopkit.accounting.journal import Journal
from shopkit.config import Settings
from shopkit.events import EventBus
from shopkit.payments.models import CAPTURED, Payment
from shopkit.payments.refunds import RefundTooLarge
from shopkit.payments.settlement import (ProviderError, RefundRequest, RefundsPaused, SettlementLedger,
                                         settle_refund)

TODAY = date(2026, 3, 10)


class Provider:
    def __init__(self, fail=0):
        self.fail, self.calls = fail, []

    def refund(self, pid, amount, key):
        self.calls.append((pid, amount, key))
        if self.fail:
            self.fail -= 1
            raise RuntimeError("provider 503")
        return f"pr_{len(self.calls)}"


class GiftCards:
    def issue(self, amount, customer_id):
        return f"GC-{amount}"


def _pay(pid, amount, created, method="card"):
    return Payment(pid, "O1", amount, "USD", method, status=CAPTURED, created=created)


def _settle(req, payments, **kw):
    kw.setdefault("provider", Provider())
    kw.setdefault("journal", Journal())
    kw.setdefault("ledger", SettlementLedger())
    return settle_refund(req, payments, customer_id="C1", today=TODAY, bus=EventBus(), sleep=lambda s: None, **kw)


def test_recent_card_payment_refunds_to_card_and_books_ledger():
    j = Journal()
    s = _settle(RefundRequest("O1", 1_000, 80, 0, "damaged", "ann"), [_pay("P1", 5_000, datetime(2026, 3, 1))], journal=j)
    assert s.to_card_cents == 1_080 and s.provider_refs == ["pr_1"]
    assert j.balance(A.CASH_CLEARING.code) == -1_080 and j.balance(A.SALES_TAX_PAYABLE.code) == -80


def test_old_card_payment_goes_to_store_credit():
    ledger = SettlementLedger()
    provider = Provider()
    s = _settle(RefundRequest("O1", 1_000, 0, 0, "damaged", "ann"), [_pay("P1", 5_000, datetime(2025, 8, 1))],
                ledger=ledger, provider=provider)
    assert s.to_store_credit_cents == 1_000 and provider.calls == [] and ledger.store_credit["C1"] == 1_000


def test_split_payment_gift_card_gets_its_share_and_remainder_goes_to_card():
    s = _settle(RefundRequest("O1", 1_001, 0, 0, "damaged", "ann"),
                [_pay("P1", 3_000, datetime(2026, 3, 1)), _pay("G1", 1_000, datetime(2026, 3, 1), "gift_card")],
                gift_cards=GiftCards())
    assert (s.to_card_cents, s.to_gift_card_cents, s.gift_card_code) == (751, 250, "GC-250")


def test_idempotency_and_too_large():
    ledger = SettlementLedger()
    provider = Provider()
    req = RefundRequest("O1", 500, 0, 0, "damaged", "ann", idempotency_key="k1")
    pays = [_pay("P1", 5_000, datetime(2026, 3, 1))]
    first = _settle(req, pays, ledger=ledger, provider=provider)
    assert _settle(req, pays, ledger=ledger, provider=provider) is first and len(provider.calls) == 1
    with pytest.raises(RefundTooLarge):
        _settle(RefundRequest("O1", 9_000, 0, 0, "damaged", "ann"), pays)


def test_provider_retries_and_kill_switch():
    provider = Provider(fail=2)
    s = _settle(RefundRequest("O1", 500, 0, 0, "damaged", "ann"), [_pay("P1", 5_000, datetime(2026, 3, 1))], provider=provider)
    assert len(provider.calls) == 3 and s.to_card_cents == 500
    with pytest.raises(ProviderError):
        _settle(RefundRequest("O1", 500, 0, 0, "x", "ann"), [_pay("P1", 5_000, datetime(2026, 3, 1))], provider=Provider(fail=9))
    with pytest.raises(RefundsPaused):
        _settle(RefundRequest("O1", 500, 0, 0, "x", "ann"), [_pay("P1", 5_000, datetime(2026, 3, 1))],
                settings=Settings(feature_flags={"refunds_paused": True}))
    old = _settle(RefundRequest("O1", 500, 0, 0, "x", "ann"), [_pay("P1", 5_000, datetime(2025, 1, 1))],
                  settings=Settings(feature_flags={"refunds_paused": True}))
    assert old.to_store_credit_cents == 500


def test_metrics_and_mixed_ledger_split():
    metrics, j = {}, Journal()
    s = _settle(RefundRequest("O1", 1_000, 100, 0, "damaged", "ann"),
                [_pay("P1", 2_000, datetime(2026, 3, 1)), _pay("P2", 2_000, datetime(2025, 1, 1))], journal=j, metrics=metrics)
    assert s.to_card_cents + s.to_store_credit_cents == 1_100 and metrics["refunds.count"] == 1
    assert j.balance(A.CUSTOMER_CREDIT.code) == s.to_store_credit_cents
