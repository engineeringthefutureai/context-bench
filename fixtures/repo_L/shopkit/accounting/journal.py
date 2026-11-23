"""The journal: balanced entries of debit and credit lines. Nothing is ever edited or deleted; a
mistake is fixed with a reversing entry (`reverse`). Closed periods refuse new entries dated inside
them (see accounting.periods)."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from shopkit.accounting.accounts import Chart
from shopkit.errors import Conflict, ShopError


class Unbalanced(ShopError):
    http_status = 422


class PeriodClosed(Conflict):
    pass


@dataclass(frozen=True)
class Line:
    account: str
    debit_cents: int = 0
    credit_cents: int = 0

    def __post_init__(self):
        if self.debit_cents < 0 or self.credit_cents < 0:
            raise ValueError("amounts are positive; use the other column instead")
        if self.debit_cents and self.credit_cents:
            raise ValueError("a line is either a debit or a credit")


def dr(account, cents: int) -> Line:
    code = account if isinstance(account, str) else account.code
    return Line(code, debit_cents=cents)


def cr(account, cents: int) -> Line:
    code = account if isinstance(account, str) else account.code
    return Line(code, credit_cents=cents)


@dataclass(frozen=True)
class Entry:
    id: int
    posted: date
    memo: str
    source: str                      # "order", "refund", "payout", "manual", ...
    source_ref: str | None
    lines: tuple[Line, ...]
    reverses: int | None = None

    @property
    def total_cents(self) -> int:
        return sum(l.debit_cents for l in self.lines)


@dataclass
class Journal:
    chart: Chart = field(default_factory=Chart)
    entries: list[Entry] = field(default_factory=list)
    closed_through: date | None = None           # last day of the latest closed period
    _refs: dict[tuple[str, str], int] = field(default_factory=dict)

    def post(self, posted: date, memo: str, lines: list[Line], *, source: str = "manual",
             source_ref: str | None = None, reverses: int | None = None) -> Entry:
        """Record a balanced entry. Zero lines are dropped. One entry per (source, source_ref): posting
        the same business event twice returns the existing entry (webhooks get retried)."""
        if source_ref is not None and (source, source_ref) in self._refs:
            return self.entries[self._refs[(source, source_ref)] - 1]
        if self.closed_through is not None and posted <= self.closed_through:
            raise PeriodClosed(f"books are closed through {self.closed_through}", posted=posted.isoformat())
        lines = [l for l in lines if l.debit_cents or l.credit_cents]
        if len(lines) < 2:
            raise Unbalanced("an entry needs at least two lines", memo=memo)
        debits = sum(l.debit_cents for l in lines)
        credits = sum(l.credit_cents for l in lines)
        if debits != credits:
            raise Unbalanced(f"debits {debits} != credits {credits}", memo=memo, debits=debits, credits=credits)
        for l in lines:
            self.chart.get(l.account)
        entry = Entry(len(self.entries) + 1, posted, memo, source, source_ref, tuple(lines), reverses)
        self.entries.append(entry)
        if source_ref is not None:
            self._refs[(source, source_ref)] = entry.id
        return entry

    def reverse(self, entry_id: int, posted: date, memo: str = "") -> Entry:
        original = self.entries[entry_id - 1]
        if any(e.reverses == entry_id for e in self.entries):
            raise Conflict(f"entry {entry_id} is already reversed", entry_id=entry_id)
        lines = [Line(l.account, debit_cents=l.credit_cents, credit_cents=l.debit_cents) for l in original.lines]
        return self.post(posted, memo or f"Reversal of #{entry_id}: {original.memo}", lines,
                         source="reversal", source_ref=str(entry_id), reverses=entry_id)

    def totals(self, start: date | None = None, end: date | None = None) -> dict[str, tuple[int, int]]:
        """account -> (debits, credits) for entries posted in [start, end]."""
        out: dict[str, list[int]] = defaultdict(lambda: [0, 0])
        for e in self.entries:
            if (start and e.posted < start) or (end and e.posted > end):
                continue
            for l in e.lines:
                out[l.account][0] += l.debit_cents
                out[l.account][1] += l.credit_cents
        return {k: (v[0], v[1]) for k, v in out.items()}

    def balance(self, code: str, end: date | None = None) -> int:
        debits, credits = self.totals(end=end).get(code, (0, 0))
        return self.chart.get(code).balance(debits, credits)

    def for_source(self, source: str, source_ref: str) -> Entry | None:
        i = self._refs.get((source, source_ref))
        return self.entries[i - 1] if i else None
