"""shopkit-admin: operator commands. Each command is a function taking parsed args and a Context, so
they can be run from cron, from the admin UI's "run now" buttons and from tests.

    shopkit-admin migrate [--status] [--to N]
    shopkit-admin import-products FILE [--dry-run]
    shopkit-admin stock-file FILE
    shopkit-admin payouts --start YYYY-MM-DD --end YYYY-MM-DD [--approve-by NAME]
    shopkit-admin reindex
    shopkit-admin fraud-expire
    shopkit-admin close-month YYYY-MM
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from shopkit.accounting.journal import Journal
from shopkit.accounting.periods import CloseBlocked, close_month
from shopkit.catalog.importer import import_products
from shopkit.catalog.models import Product
from shopkit.db.connection import Database
from shopkit.db.migrations import MigrationRunner
from shopkit.fraud.scoring import ReviewQueue
from shopkit.inventory.erp_import import apply_stock_file
from shopkit.inventory.stock import Stock
from shopkit.payouts.batching import Rollover, approve, build_payout_batch
from shopkit.payouts.holds import HoldBook
from shopkit.search.index import SearchIndex
from shopkit.utils.dates import utc_now

log = logging.getLogger("shopkit.admin")


@dataclass
class Context:
    """Everything the commands touch. The real entry point builds it from settings; tests build it by hand."""
    db: Database | None = None
    products: dict[str, Product] = field(default_factory=dict)
    stock: dict[str, Stock] = field(default_factory=dict)
    journal: Journal = field(default_factory=Journal)
    reviews: ReviewQueue | None = None
    search: SearchIndex = field(default_factory=SearchIndex)
    sellers: list = field(default_factory=list)
    earnings: list = field(default_factory=list)
    adjustments: list = field(default_factory=list)
    holds: HoldBook = field(default_factory=HoldBook)
    rollover: Rollover = field(default_factory=Rollover)
    out: list[str] = field(default_factory=list)

    def say(self, text: str) -> None:
        self.out.append(text)


def cmd_migrate(args, ctx: Context) -> int:
    if ctx.db is None:
        ctx.say("no database configured")
        return 2
    runner = MigrationRunner(ctx.db)
    if args.status:
        for row in runner.status():
            ctx.say(f"{row['version']:04d} {row['name']:<30} {'applied' if row['applied'] else 'pending'}")
        return 0
    done = runner.apply_all(up_to=args.to)
    ctx.say(f"applied {len(done)} migrations" + (f": {', '.join(f'{v:04d}' for v in done)}" if done else ""))
    return 0


def cmd_import_products(args, ctx: Context) -> int:
    text = Path(args.file).read_text()
    report = import_products(text, existing_skus=set(ctx.products))
    for err in report.errors:
        ctx.say(f"error: {err}")
    if not args.dry_run:
        for p in report.created:
            ctx.products[p.sku] = p
            ctx.search.add(p)
    ctx.say(f"{len(report.created)} products {'would be ' if args.dry_run else ''}created, {len(report.errors)} errors")
    return 0 if report.ok else 1


def cmd_stock_file(args, ctx: Context) -> int:
    result = apply_stock_file(Path(args.file).read_text(), ctx.stock, list(ctx.products))
    for (wh, sku), (old, new) in sorted(result.changed.items()):
        ctx.say(f"{wh} {sku}: {old} -> {new}")
    for w in result.warnings:
        ctx.say(f"warning: {w}")
    ctx.say(f"{len(result.changed)} stock levels changed")
    return 0


def cmd_payouts(args, ctx: Context) -> int:
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if end < start:
        ctx.say("end is before start")
        return 2
    result = build_payout_batch(ctx.sellers, ctx.earnings, ctx.adjustments, start, end, holds=ctx.holds,
                                rollover=ctx.rollover)
    for p in result.payouts:
        if args.approve_by and p.status == "draft":
            approve(p, args.approve_by)
        ctx.say(f"{p.id} {p.seller_id} {p.status} {p.net_cents}")
    for sid, why in sorted(result.skipped.items()):
        ctx.say(f"skipped {sid}: {why}")
    ctx.say(json.dumps({k: v for k, v in result.stats.items() if not k.endswith("_ms")}, sort_keys=True))
    return 0


def cmd_reindex(args, ctx: Context) -> int:
    ctx.search = SearchIndex()
    for p in ctx.products.values():
        ctx.search.add(p)
    ctx.say(f"indexed {len(ctx.products)} products")
    return 0


def cmd_fraud_expire(args, ctx: Context) -> int:
    if ctx.reviews is None:
        ctx.say("no review queue")
        return 2
    done = ctx.reviews.expire(utc_now().replace(tzinfo=None))
    ctx.say(f"auto-accepted {len(done)} reviews")
    return 0


def cmd_close_month(args, ctx: Context) -> int:
    try:
        year, month = (int(x) for x in args.month.split("-"))
    except ValueError:
        ctx.say("month is YYYY-MM")
        return 2
    try:
        end = close_month(ctx.journal, year, month)
    except CloseBlocked as e:
        ctx.say(f"blocked: {e}")
        return 1
    ctx.say(f"closed through {end}")
    return 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shopkit-admin")
    sub = p.add_subparsers(dest="command", required=True)
    m = sub.add_parser("migrate")
    m.add_argument("--status", action="store_true")
    m.add_argument("--to", type=int)
    m.set_defaults(func=cmd_migrate)
    ip = sub.add_parser("import-products")
    ip.add_argument("file")
    ip.add_argument("--dry-run", action="store_true")
    ip.set_defaults(func=cmd_import_products)
    sf = sub.add_parser("stock-file")
    sf.add_argument("file")
    sf.set_defaults(func=cmd_stock_file)
    po = sub.add_parser("payouts")
    po.add_argument("--start", required=True)
    po.add_argument("--end", required=True)
    po.add_argument("--approve-by")
    po.set_defaults(func=cmd_payouts)
    sub.add_parser("reindex").set_defaults(func=cmd_reindex)
    sub.add_parser("fraud-expire").set_defaults(func=cmd_fraud_expire)
    cm = sub.add_parser("close-month")
    cm.add_argument("month")
    cm.set_defaults(func=cmd_close_month)
    return p


def main(argv: list[str] | None = None, ctx: Context | None = None) -> int:
    ctx = ctx or Context()
    args = parser().parse_args(argv)
    started = datetime.now()
    try:
        code = args.func(args, ctx)
    except FileNotFoundError as e:
        ctx.say(f"no such file: {e.filename}")
        code = 2
    log.info("shopkit-admin %s finished with %d in %.1fs", args.command, code, (datetime.now() - started).total_seconds())
    if ctx is not None and argv is None:
        print("\n".join(ctx.out))
    return code


if __name__ == "__main__":
    sys.exit(main())
