"""Chart of accounts. Codes follow the finance team's numbering: 1xxx assets, 2xxx liabilities,
3xxx equity, 4xxx revenue, 5xxx-6xxx expenses. Normal balance: debit for assets and expenses, credit
for liabilities, equity and revenue."""
from __future__ import annotations

from dataclasses import dataclass

from shopkit.errors import NotFound

ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE = "asset", "liability", "equity", "revenue", "expense"
DEBIT_NORMAL = {ASSET, EXPENSE}


class UnknownAccount(NotFound):
    pass


@dataclass(frozen=True)
class Account:
    code: str
    name: str
    kind: str

    @property
    def debit_normal(self) -> bool:
        return self.kind in DEBIT_NORMAL

    def balance(self, debits: int, credits: int) -> int:
        """Balance in the account's normal direction (positive = normal)."""
        return debits - credits if self.debit_normal else credits - debits


# The accounts the posting rules use. Finance can add more (e.g. per-channel revenue) in the ledger
# admin; these codes must exist in every ledger.
CASH_CLEARING = Account("1010", "Payment provider clearing", ASSET)
BANK = Account("1000", "Bank", ASSET)
RECEIVABLE_MARKETPLACE = Account("1200", "Receivable from marketplace channels", ASSET)
INVENTORY = Account("1300", "Inventory", ASSET)
SALES_TAX_PAYABLE = Account("2100", "Sales tax payable", LIABILITY)
SELLER_PAYABLE = Account("2200", "Payable to sellers", LIABILITY)
GIFT_CARD_LIABILITY = Account("2300", "Gift cards outstanding", LIABILITY)
CUSTOMER_CREDIT = Account("2310", "Store credit outstanding", LIABILITY)
DEFERRED_REVENUE = Account("2400", "Deferred subscription revenue", LIABILITY)
RETAINED_EARNINGS = Account("3100", "Retained earnings", EQUITY)
SALES_REVENUE = Account("4000", "Product sales", REVENUE)
SHIPPING_REVENUE = Account("4100", "Shipping charged to customers", REVENUE)
COMMISSION_REVENUE = Account("4200", "Marketplace commission", REVENUE)
SUBSCRIPTION_REVENUE = Account("4300", "Subscription revenue", REVENUE)
SALES_DISCOUNTS = Account("4900", "Discounts and promotions", REVENUE)      # contra-revenue (debit)
REFUNDS = Account("4910", "Refunds", REVENUE)                              # contra-revenue (debit)
COGS = Account("5000", "Cost of goods sold", EXPENSE)
PAYMENT_FEES = Account("6100", "Payment processing fees", EXPENSE)
SHIPPING_COSTS = Account("6200", "Carrier costs", EXPENSE)
CHARGEBACK_LOSSES = Account("6300", "Chargeback losses", EXPENSE)

STANDARD = [CASH_CLEARING, BANK, RECEIVABLE_MARKETPLACE, INVENTORY, SALES_TAX_PAYABLE, SELLER_PAYABLE,
            GIFT_CARD_LIABILITY, CUSTOMER_CREDIT, DEFERRED_REVENUE, RETAINED_EARNINGS, SALES_REVENUE,
            SHIPPING_REVENUE, COMMISSION_REVENUE, SUBSCRIPTION_REVENUE, SALES_DISCOUNTS, REFUNDS, COGS,
            PAYMENT_FEES, SHIPPING_COSTS, CHARGEBACK_LOSSES]


class Chart:
    def __init__(self, accounts: list[Account] | None = None):
        self._by_code: dict[str, Account] = {}
        for a in accounts if accounts is not None else STANDARD:
            self.add(a)

    def add(self, account: Account) -> None:
        if account.code in self._by_code:
            raise ValueError(f"duplicate account {account.code}")
        if not account.code.isdigit() or len(account.code) != 4:
            raise ValueError("account codes are four digits")
        self._by_code[account.code] = account

    def get(self, code: str) -> Account:
        try:
            return self._by_code[code]
        except KeyError:
            raise UnknownAccount(f"no account {code}", code=code) from None

    def __contains__(self, code: str) -> bool:
        return code in self._by_code

    def by_kind(self, kind: str) -> list[Account]:
        return sorted((a for a in self._by_code.values() if a.kind == kind), key=lambda a: a.code)

    def all(self) -> list[Account]:
        return sorted(self._by_code.values(), key=lambda a: a.code)
