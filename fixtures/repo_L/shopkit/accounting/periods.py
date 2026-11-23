"""Monthly period close and the reports finance reads from the journal: trial balance, profit and
loss, and the balance sheet."""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date

from shopkit.accounting import accounts as A
from shopkit.accounting.journal import Journal, cr, dr
from shopkit.errors import Conflict


class CloseBlocked(Conflict):
    pass


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


@dataclass
class TrialBalanceRow:
    code: str
    name: str
    debit_cents: int
    credit_cents: int


@dataclass
class TrialBalance:
    rows: list[TrialBalanceRow] = field(default_factory=list)

    @property
    def balanced(self) -> bool:
        return sum(r.debit_cents for r in self.rows) == sum(r.credit_cents for r in self.rows)


def trial_balance(journal: Journal, end: date) -> TrialBalance:
    """Net balance of every account with activity up to `end`, in its debit or credit column."""
    tb = TrialBalance()
    for code, (debits, credits) in sorted(journal.totals(end=end).items()):
        net = debits - credits
        if net == 0:
            continue
        account = journal.chart.get(code)
        tb.rows.append(TrialBalanceRow(code, account.name, max(net, 0), max(-net, 0)))
    return tb


@dataclass
class ProfitAndLoss:
    revenue_cents: int
    contra_revenue_cents: int
    expenses_cents: int

    @property
    def net_revenue_cents(self) -> int:
        return self.revenue_cents - self.contra_revenue_cents

    @property
    def profit_cents(self) -> int:
        return self.net_revenue_cents - self.expenses_cents


CONTRA_REVENUE = {A.SALES_DISCOUNTS.code, A.REFUNDS.code}


def profit_and_loss(journal: Journal, start: date, end: date) -> ProfitAndLoss:
    revenue = contra = expenses = 0
    for code, (debits, credits) in journal.totals(start, end).items():
        account = journal.chart.get(code)
        if account.kind == A.REVENUE:
            if code in CONTRA_REVENUE:
                contra += debits - credits
            else:
                revenue += credits - debits
        elif account.kind == A.EXPENSE:
            expenses += debits - credits
    return ProfitAndLoss(revenue, contra, expenses)


def balance_sheet(journal: Journal, end: date) -> dict[str, int]:
    """Totals per kind for assets, liabilities and equity (equity includes unclosed profit)."""
    out = {A.ASSET: 0, A.LIABILITY: 0, A.EQUITY: 0}
    profit = 0
    for code, (debits, credits) in journal.totals(end=end).items():
        account = journal.chart.get(code)
        if account.kind in out:
            out[account.kind] += account.balance(debits, credits)
        elif account.kind == A.REVENUE:
            profit += credits - debits
        else:
            profit -= debits - credits
    out[A.EQUITY] += profit
    return out


def close_month(journal: Journal, year: int, month: int, *, open_items: int = 0) -> date:
    """Close a month: zero the revenue and expense accounts into retained earnings with one closing
    entry dated the last day, then refuse entries dated inside it. `open_items` (unreconciled
    provider settlements) must be 0. Months close in order."""
    end = month_end(year, month)
    if journal.closed_through is not None and end <= journal.closed_through:
        raise CloseBlocked(f"{year}-{month:02d} is already closed")
    if journal.closed_through is not None:
        expected = journal.closed_through.month % 12 + 1
        if month != expected:
            raise CloseBlocked(f"close {calendar.month_name[expected]} first")
    if open_items:
        raise CloseBlocked(f"{open_items} unreconciled settlements", open_items=open_items)
    start = date(year, month, 1)
    lines, net = [], 0
    for code, (debits, credits) in sorted(journal.totals(start, end).items()):
        account = journal.chart.get(code)
        if account.kind not in (A.REVENUE, A.EXPENSE) or debits == credits:
            continue
        if debits > credits:
            lines.append(cr(code, debits - credits))
            net -= debits - credits
        else:
            lines.append(dr(code, credits - debits))
            net += credits - debits
    if lines:
        lines.append(cr(A.RETAINED_EARNINGS, net) if net > 0 else dr(A.RETAINED_EARNINGS, -net))
        journal.post(end, f"Close {year}-{month:02d}", lines, source="close", source_ref=f"{year}-{month:02d}")
    journal.closed_through = end
    return end
