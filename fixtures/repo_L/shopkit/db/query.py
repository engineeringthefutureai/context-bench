"""A small SELECT builder for the repositories. Builds SQL text and a parameter tuple; it never
interpolates values, so user input can't end up in the SQL.

    q = Select("orders").where("status = ?", "paid").where_in("currency", ["USD", "EUR"])
    q = q.order_by("created_at", desc=True).limit(50)
    rows = db.query(*q.build())
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def ident(name: str) -> str:
    """A table or column name, checked: identifiers can't be parameters, so they are validated."""
    if not _IDENT.match(name):
        raise ValueError(f"not a valid identifier: {name!r}")
    return name


@dataclass(frozen=True)
class Select:
    table: str
    columns: tuple[str, ...] = ("*",)
    conditions: tuple[str, ...] = ()
    params: tuple = ()
    joins: tuple[str, ...] = ()
    order: tuple[str, ...] = ()
    group: tuple[str, ...] = ()
    max_rows: int | None = None
    skip: int = 0

    def __post_init__(self):
        ident(self.table)

    def select(self, *columns: str) -> "Select":
        for c in columns:
            if c != "*" and " AS " not in c.upper() and "(" not in c:
                ident(c)
        return replace(self, columns=columns or ("*",))

    def where(self, condition: str, *params) -> "Select":
        if condition.count("?") != len(params):
            raise ValueError(f"{condition!r} has {condition.count('?')} placeholders, got {len(params)} values")
        return replace(self, conditions=self.conditions + (condition,), params=self.params + params)

    def where_in(self, column: str, values: list | tuple) -> "Select":
        values = tuple(values)
        if not values:
            return replace(self, conditions=self.conditions + ("0 = 1",))     # IN () matches nothing
        marks = ", ".join("?" for _ in values)
        return replace(self, conditions=self.conditions + (f"{ident(column)} IN ({marks})",),
                       params=self.params + values)

    def where_between(self, column: str, low, high) -> "Select":
        return self.where(f"{ident(column)} BETWEEN ? AND ?", low, high)

    def join(self, table: str, on: str, kind: str = "JOIN") -> "Select":
        if kind not in ("JOIN", "LEFT JOIN"):
            raise ValueError(f"unsupported join {kind!r}")
        return replace(self, joins=self.joins + (f"{kind} {ident(table)} ON {on}",))

    def order_by(self, column: str, desc: bool = False) -> "Select":
        return replace(self, order=self.order + (f"{ident(column)} {'DESC' if desc else 'ASC'}",))

    def group_by(self, *columns: str) -> "Select":
        return replace(self, group=self.group + tuple(ident(c) for c in columns))

    def limit(self, n: int, offset: int = 0) -> "Select":
        if n < 0 or offset < 0:
            raise ValueError("limit and offset must be >= 0")
        return replace(self, max_rows=n, skip=offset)

    def build(self) -> tuple[str, tuple]:
        sql = f"SELECT {', '.join(self.columns)} FROM {self.table}"
        if self.joins:
            sql += " " + " ".join(self.joins)
        if self.conditions:
            sql += " WHERE " + " AND ".join(f"({c})" for c in self.conditions)
        if self.group:
            sql += " GROUP BY " + ", ".join(self.group)
        if self.order:
            sql += " ORDER BY " + ", ".join(self.order)
        if self.max_rows is not None:
            sql += f" LIMIT {int(self.max_rows)} OFFSET {int(self.skip)}"
        return sql, self.params

    def count(self) -> tuple[str, tuple]:
        """The same filter as SELECT COUNT(*), without ordering or paging."""
        base = replace(self, columns=("COUNT(*)",), order=(), max_rows=None, skip=0)
        return base.build()


def insert(table: str, values: dict) -> tuple[str, tuple]:
    cols = [ident(c) for c in values]
    marks = ", ".join("?" for _ in cols)
    return f"INSERT INTO {ident(table)} ({', '.join(cols)}) VALUES ({marks})", tuple(values.values())


def update(table: str, values: dict, where: str, *params) -> tuple[str, tuple]:
    if not values:
        raise ValueError("nothing to update")
    sets = ", ".join(f"{ident(c)} = ?" for c in values)
    return f"UPDATE {ident(table)} SET {sets} WHERE {where}", tuple(values.values()) + params


@dataclass
class Page:
    """One page of results plus what the API needs to link the next one."""
    rows: list = field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 50

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.rows) < self.total

    @property
    def next_offset(self) -> int | None:
        return self.offset + self.limit if self.has_more else None
