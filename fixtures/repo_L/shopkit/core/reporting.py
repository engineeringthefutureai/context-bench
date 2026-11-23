# -*- coding: utf-8 -*-
#
# Reporting: fiscal calendar, sales tax, inventory valuation, and the weekly report emailed every
# Monday (ReportGenerator, run by cron).
#
import csv
import io
import logging
import math
import os
from collections import OrderedDict, defaultdict
from datetime import date, datetime, timedelta

log = logging.getLogger("reporting")

REPORT_DIR = os.environ.get("REPORT_DIR", "/var/reports")
FISCAL_YEAR_START_MONTH = 2           # February
FISCAL_YEAR_START_DAY = 1
DEFAULT_CURRENCY = "USD"
_cache = {}


# ---- fiscal calendar ------------------------------------------------------------------------------
def fiscalYearStart(d):
    """first day of the fiscal year containing d. FY2026 runs 2026-02-01 .. 2027-01-31 and is named
    after the calendar year it *starts* in."""
    start = date(d.year, FISCAL_YEAR_START_MONTH, FISCAL_YEAR_START_DAY)
    if d < start:
        start = date(d.year - 1, FISCAL_YEAR_START_MONTH, FISCAL_YEAR_START_DAY)
    return start


def fiscalYear(d):
    return fiscalYearStart(d).year


def fiscalWeek(d):
    """Fiscal week label like 'FY2026-W07'.

    Week 1 is the week (Monday-Sunday) that contains the first day of the fiscal year, so week 1 is
    often shorter than 7 days and may start in January. Weeks are counted from that Monday. The last
    days of January belong to the previous fiscal year, so 2027-01-31 is in FY2026 (week 53 in some
    years). Not the ISO week: finance's calendar differs from shopkit.utils.dates.iso_week_label."""
    if isinstance(d, datetime):
        d = d.date()
    start = fiscalYearStart(d)
    firstMonday = start - timedelta(days=start.weekday())
    week = (d - firstMonday).days // 7 + 1
    return "FY%d-W%02d" % (start.year, week)


def fiscalPeriod(d):
    """fiscal month 1-12 (P01 = February)."""
    if isinstance(d, datetime):
        d = d.date()
    return (d.month - FISCAL_YEAR_START_MONTH) % 12 + 1


def fiscalQuarter(d):
    return "FY%d-Q%d" % (fiscalYear(d), (fiscalPeriod(d) - 1) // 3 + 1)


def periodBounds(fy, period):
    """(first day, last day) of fiscal period `period` (1-12) of fiscal year `fy`."""
    month = (FISCAL_YEAR_START_MONTH - 1 + period - 1) % 12 + 1
    year = fy if month >= FISCAL_YEAR_START_MONTH else fy + 1
    first = date(year, month, 1)
    nextFirst = date(year + (month == 12), month % 12 + 1, 1)
    return first, nextFirst - timedelta(days=1)


# ---- sales tax report (quarterly filing) -------------------------------------------------------------
# States where we have nexus; others are reported as "no nexus" and not summed into the filing total.
NEXUS_STATES = ("CA", "NY", "TX", "WA", "IL", "FL", "PA", "NJ")


def salesTaxByState(orders, start, end):
    """orders: iterable of dicts {date, state, taxableCents, taxCents, refundedTaxCents?}

    Returns OrderedDict state -> {orders, taxable, tax, refunded, net} in *dollars* (float, rounded
    to cents), sorted by state, plus a 'TOTAL' row over nexus states only. Orders outside [start, end]
    are skipped. Refunds reduce the tax of the period the *refund* happened in, which is how the
    filings work, but this report only has the order date, so refunded tax is subtracted from the
    order's period (known difference, finance adjusts by hand)."""
    agg = defaultdict(lambda: {"orders": 0, "taxable": 0, "tax": 0, "refunded": 0})
    for o in orders:
        d = o["date"]
        if isinstance(d, datetime):
            d = d.date()
        if d < start or d > end:
            continue
        st = (o.get("state") or "??").upper()
        a = agg[st]
        a["orders"] += 1
        a["taxable"] += o.get("taxableCents", 0)
        a["tax"] += o.get("taxCents", 0)
        a["refunded"] += o.get("refundedTaxCents", 0)
    out = OrderedDict()
    tot = {"orders": 0, "taxable": 0, "tax": 0, "refunded": 0}
    for st in sorted(agg):
        a = agg[st]
        row = {"orders": a["orders"], "taxable": a["taxable"] / 100.0, "tax": a["tax"] / 100.0,
               "refunded": a["refunded"] / 100.0, "net": (a["tax"] - a["refunded"]) / 100.0,
               "nexus": st in NEXUS_STATES}
        out[st] = row
        if st in NEXUS_STATES:
            for k in tot:
                tot[k] += a[k]
    out["TOTAL"] = {"orders": tot["orders"], "taxable": tot["taxable"] / 100.0, "tax": tot["tax"] / 100.0,
                    "refunded": tot["refunded"] / 100.0, "net": (tot["tax"] - tot["refunded"]) / 100.0,
                    "nexus": True}
    return out


def salesTaxCsv(report):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["state", "nexus", "orders", "taxable", "tax", "refunded", "net"])
    for st, r in report.items():
        w.writerow([st, "Y" if r["nexus"] else "N", r["orders"], "%.2f" % r["taxable"], "%.2f" % r["tax"],
                    "%.2f" % r["refunded"], "%.2f" % r["net"]])
    return buf.getvalue()


# ---- inventory valuation (FIFO) ---------------------------------------------------------------------
def inventoryValuationFifo(receipts, issues, asOf=None):
    """Value remaining stock per SKU with FIFO.

    receipts: list of (date, sku, qty, unitCostCents) - purchase receipts
    issues:   list of (date, sku, qty)                - units sold/written off
    Issues consume the oldest receipts first. Issues beyond what was received are ignored (the
    count was wrong; ops fixes the stock figure separately) and reported in 'negative'.
    Returns {sku: {"qty": int, "valueCents": int, "avgCostCents": float}} and a dict of negative skus."""
    asOf = asOf or date.max
    layers = defaultdict(list)          # sku -> [[qty, cost], ...] oldest first
    for d, sku, qty, cost in sorted(receipts, key=lambda r: (r[0], r[1])):
        if d <= asOf:
            layers[sku].append([qty, cost])
    negative = {}
    for d, sku, qty in sorted(issues, key=lambda r: (r[0], r[1])):
        if d > asOf:
            continue
        left = qty
        while left > 0 and layers[sku]:
            layer = layers[sku][0]
            take = min(layer[0], left)
            layer[0] -= take
            left -= take
            if layer[0] == 0:
                layers[sku].pop(0)
        if left > 0:
            negative[sku] = negative.get(sku, 0) + left
    out = {}
    for sku, ls in layers.items():
        q = sum(l[0] for l in ls)
        if q == 0:
            continue
        v = sum(l[0] * l[1] for l in ls)
        out[sku] = {"qty": q, "valueCents": v, "avgCostCents": round(v / float(q), 2)}
    return out, negative


# ---- formatting helpers ---------------------------------------------------------------------------------
def money(x, cur=DEFAULT_CURRENCY):
    sym = {"USD": "$", "EUR": "EUR ", "GBP": "GBP "}.get(cur, cur + " ")
    if x is None:
        return "-"
    neg = x < 0
    s = "{:,.2f}".format(abs(x))
    return ("-" if neg else "") + sym + s


def pct(a, b):
    if not b:
        return "n/a"
    return "%.1f%%" % (100.0 * a / b)


def asciiTable(rows, header=None, align=None):
    rows = [list(map(str, r)) for r in rows]
    if header:
        rows = [list(map(str, header))] + rows
    if not rows:
        return ""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    align = align or ["l"] + ["r"] * (len(widths) - 1)
    out = []
    for n, r in enumerate(rows):
        cells = []
        for i, c in enumerate(r):
            cells.append(c.ljust(widths[i]) if align[i] == "l" else c.rjust(widths[i]))
        out.append("  ".join(cells))
        if header and n == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)


def htmlTable(rows, header=None):
    h = ["<table border='1' cellpadding='4' cellspacing='0'>"]
    if header:
        h.append("<tr>" + "".join("<th>%s</th>" % c for c in header) + "</tr>")
    for r in rows:
        h.append("<tr>" + "".join("<td>%s</td>" % c for c in r) + "</tr>")
    h.append("</table>")
    return "\n".join(h)


# ---- the Monday report --------------------------------------------------------------------------------
class ReportGenerator(object):
    """builds the weekly report from raw order dicts:
        {id, date (datetime), customer, total (dollars), items: [{sku, qty, price}], channel,
         status, country, refunded (dollars)}
    """

    def __init__(self, orders, products=None, outputDir=None, currency=DEFAULT_CURRENCY):
        self.orders = orders or []
        self.products = products or {}
        self.outputDir = outputDir or REPORT_DIR
        self.currency = currency
        self.warnings = []

    def _valid(self):
        return [o for o in self.orders if o.get("status") not in ("cancelled", "error", "test")]

    def weekBounds(self, anyDay):
        start = anyDay - timedelta(days=anyDay.weekday())
        return start, start + timedelta(days=6)

    def ordersBetween(self, start, end):
        out = []
        for o in self._valid():
            d = o["date"].date() if isinstance(o["date"], datetime) else o["date"]
            if start <= d <= end:
                out.append(o)
        return out

    def revenue(self, orders):
        r = 0.0
        for o in orders:
            r += (o.get("total") or 0.0) - (o.get("refunded") or 0.0)
        return round(r, 2)

    def topProducts(self, orders, n=10):
        units = defaultdict(int)
        rev = defaultdict(float)
        for o in orders:
            for it in o.get("items", []):
                units[it["sku"]] += it["qty"]
                rev[it["sku"]] += it["qty"] * it["price"]
        ranked = sorted(units.keys(), key=lambda s: (-rev[s], s))[:n]
        return [(s, self.products.get(s, {}).get("title", s), units[s], round(rev[s], 2)) for s in ranked]

    def topCustomers(self, orders, n=10):
        spend = defaultdict(float)
        cnt = defaultdict(int)
        for o in orders:
            c = o.get("customer") or "guest"
            if c == "guest":
                continue
            spend[c] += o.get("total") or 0.0
            cnt[c] += 1
        ranked = sorted(spend.items(), key=lambda kv: -kv[1])[:n]
        return [(c, cnt[c], round(s, 2)) for c, s in ranked]

    def byChannel(self, orders):
        ch = defaultdict(lambda: [0, 0.0])
        for o in orders:
            k = o.get("channel") or "web"
            ch[k][0] += 1
            ch[k][1] += o.get("total") or 0.0
        return OrderedDict(sorted(ch.items(), key=lambda kv: -kv[1][1]))

    def byCountry(self, orders, top=8):
        c = defaultdict(float)
        for o in orders:
            c[(o.get("country") or "??").upper()] += o.get("total") or 0.0
        items = sorted(c.items(), key=lambda kv: -kv[1])
        head, rest = items[:top], items[top:]
        if rest:
            head.append(("other", sum(v for _, v in rest)))
        return head

    def cohorts(self, months=6, asOf=None):
        """repeat-purchase cohorts: for customers whose first order was in month M, the share who
        ordered again in M+1..M+k. Returns rows [cohortMonth, size, m1%, m2%, ...]"""
        asOf = asOf or datetime.utcnow()
        first = {}
        byCust = defaultdict(list)
        for o in self._valid():
            c = o.get("customer")
            if not c:
                continue
            d = o["date"]
            byCust[c].append(d)
            if c not in first or d < first[c]:
                first[c] = d
        groups = defaultdict(list)
        for c, d in first.items():
            groups[(d.year, d.month)].append(c)
        rows = []
        for (y, m) in sorted(groups)[-months:]:
            members = groups[(y, m)]
            row = ["%04d-%02d" % (y, m), len(members)]
            for k in range(1, months + 1):
                ty, tm = y + (m - 1 + k) // 12, (m - 1 + k) % 12 + 1
                if (ty, tm) > (asOf.year, asOf.month):
                    row.append("")
                    continue
                again = 0
                for c in members:
                    if any(d.year == ty and d.month == tm for d in byCust[c]):
                        again += 1
                row.append(pct(again, len(members)))
            rows.append(row)
        return rows

    def generate(self, weekOf=None, html=False, compareWeeks=4):
        """the full Monday report as text (or html). Long; grew one section at a time since 2018."""
        weekOf = weekOf or (datetime.utcnow().date() - timedelta(days=7))
        start, end = self.weekBounds(weekOf)
        cur = self.ordersBetween(start, end)
        prevStart, prevEnd = start - timedelta(days=7), end - timedelta(days=7)
        prev = self.ordersBetween(prevStart, prevEnd)
        sections = []

        # -- headline
        rev, prevRev = self.revenue(cur), self.revenue(prev)
        change = pct(rev - prevRev, prevRev) if prevRev else "n/a"
        aov = rev / len(cur) if cur else 0.0
        headline = [
            ["Week", "%s .. %s (%s)" % (start.isoformat(), end.isoformat(), fiscalWeek(start))],
            ["Orders", "%d (prev %d)" % (len(cur), len(prev))],
            ["Revenue", "%s (prev %s, %s)" % (money(rev, self.currency), money(prevRev, self.currency), change)],
            ["Avg order", money(aov, self.currency)],
        ]
        refunds = sum((o.get("refunded") or 0.0) for o in cur)
        if refunds:
            headline.append(["Refunded", money(refunds, self.currency)])
        sections.append(("Headline", None, headline))

        # -- trend
        trend = []
        for k in range(compareWeeks, 0, -1):
            s, e = start - timedelta(days=7 * k), end - timedelta(days=7 * k)
            os_ = self.ordersBetween(s, e)
            trend.append([fiscalWeek(s), len(os_), money(self.revenue(os_), self.currency)])
        trend.append([fiscalWeek(start), len(cur), money(rev, self.currency)])
        sections.append(("Trend", ["week", "orders", "revenue"], trend))

        # -- products
        tp = self.topProducts(cur)
        sections.append(("Top products", ["sku", "title", "units", "revenue"],
                         [[s, t[:30], u, money(r, self.currency)] for s, t, u, r in tp]))

        # -- customers
        tc = self.topCustomers(cur)
        sections.append(("Top customers", ["customer", "orders", "spend"],
                         [[c, n, money(s, self.currency)] for c, n, s in tc]))

        # -- channels
        ch = self.byChannel(cur)
        sections.append(("Channels", ["channel", "orders", "revenue", "share"],
                         [[k, v[0], money(v[1], self.currency), pct(v[1], rev)] for k, v in ch.items()]))

        # -- countries
        sections.append(("Countries", ["country", "revenue"],
                         [[c, money(v, self.currency)] for c, v in self.byCountry(cur)]))

        # -- cohorts (expensive, cached per day)
        key = ("cohorts", datetime.utcnow().date())
        if key not in _cache:
            try:
                _cache[key] = self.cohorts()
            except Exception as e:
                self.warnings.append("cohorts failed: %s" % e)
                _cache[key] = []
        if _cache[key]:
            sections.append(("Repeat purchase cohorts", ["cohort", "size"] + ["m%d" % i for i in range(1, 7)],
                             _cache[key]))

        # -- data quality
        dq = []
        noCustomer = sum(1 for o in cur if not o.get("customer"))
        if noCustomer:
            dq.append(["orders without customer", noCustomer])
        zero = sum(1 for o in cur if not o.get("total"))
        if zero:
            dq.append(["orders with zero total", zero])
        unknownSku = set()
        for o in cur:
            for it in o.get("items", []):
                if self.products and it["sku"] not in self.products:
                    unknownSku.add(it["sku"])
        if unknownSku:
            dq.append(["unknown skus", ", ".join(sorted(unknownSku)[:10])])
        if dq:
            sections.append(("Data quality", None, dq))

        if html:
            parts = ["<h2>Weekly report %s</h2>" % fiscalWeek(start)]
            for title, header, rows in sections:
                parts.append("<h3>%s</h3>" % title)
                parts.append(htmlTable(rows, header))
            if self.warnings:
                parts.append("<p style='color:#a00'>%s</p>" % "<br>".join(self.warnings))
            return "\n".join(parts)
        parts = ["WEEKLY REPORT %s" % fiscalWeek(start), "=" * 40]
        for title, header, rows in sections:
            parts.append("")
            parts.append(title.upper())
            parts.append(asciiTable(rows, header))
        if self.warnings:
            parts.append("")
            parts.append("WARNINGS: " + "; ".join(self.warnings))
        return "\n".join(parts)

    def writeFiles(self, weekOf=None):
        """write .txt and .html into outputDir; returns the paths. Errors are logged, not raised (cron)."""
        paths = []
        try:
            if not os.path.isdir(self.outputDir):
                os.makedirs(self.outputDir)
            label = fiscalWeek(weekOf or datetime.utcnow().date())
            for ext, html in (("txt", False), ("html", True)):
                p = os.path.join(self.outputDir, "weekly-%s.%s" % (label, ext))
                with open(p, "w") as f:
                    f.write(self.generate(weekOf, html=html))
                paths.append(p)
        except Exception as e:
            log.error("writing report failed: %s" % e)
        return paths


def clearCache():
    _cache.clear()


def stockAging(lots, asOf=None, buckets=(30, 90, 180, 365)):
    """units per age bucket from inventory lots [(receivedDate, sku, qty)] -> {sku: [b1, b2, ..., older]}"""
    asOf = asOf or date.today()
    out = defaultdict(lambda: [0] * (len(buckets) + 1))
    for received, sku, qty in lots:
        age = (asOf - received).days
        idx = len(buckets)
        for i, b in enumerate(buckets):
            if age <= b:
                idx = i
                break
        out[sku][idx] += qty
    return dict(out)


def sellThrough(received, sold):
    """sold / received as a percentage per sku (dict in, dict out); skus with nothing received are skipped"""
    out = {}
    for sku, r in received.items():
        if r:
            out[sku] = round(100.0 * sold.get(sku, 0) / r, 1)
    return out


def weeksOfSupply(onHand, weeklyUnits):
    if weeklyUnits <= 0:
        return math.inf
    return round(onHand / float(weeklyUnits), 1)
