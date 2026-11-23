"""Pick waves: paid orders are grouped into waves per site and allocated to bins before pickers
get their lists. Released waves go to the WMS (core.order_engine.handleWmsCallback brings status back)."""
from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from shopkit.config import SETTINGS, Settings
from shopkit.events import BUS, EventBus
from shopkit.orders.models import Order
from shopkit.warehouse.sites import Lot, Site, SiteStock

log = logging.getLogger(__name__)

EXPRESS, STANDARD = "express", "standard"
MAX_WAVE_ORDERS = 200
MAX_WAVE_LINES = 1200
SHORT_PICK_TOLERANCE = 0          # units an order may be short and still ship (ops set it to 0 after INC-2418)
OVERSELL_ALERT_UNITS = 5


@dataclass(frozen=True)
class WaveOrder:
    order: Order
    service: str = STANDARD                 # express | standard
    paid_at: datetime | None = None
    country: str = "US"
    gift: bool = False
    hold: str = ""                          # fraud review, address check... non-empty = not allocatable


@dataclass
class Allocation:
    order_id: str
    sku: str
    site: str
    bin: str
    qty: int
    lot_expires: date | None = None


@dataclass
class Wave:
    id: str
    site: str
    service: str
    created: datetime
    allocations: list[Allocation] = field(default_factory=list)
    orders: list[str] = field(default_factory=list)
    released: bool = False


@dataclass
class WaveResult:
    waves: list[Wave] = field(default_factory=list)
    backordered: dict[str, dict[str, int]] = field(default_factory=dict)    # order -> sku -> missing units
    skipped: dict[str, str] = field(default_factory=dict)                   # order -> reason
    stats: dict[str, float] = field(default_factory=dict)


def allocate_wave(orders: list[WaveOrder], sites: list[Site], stock: SiteStock, *, today: date,
                  hazmat_skus: frozenset[str] = frozenset(), now: datetime | None = None,
                  settings: Settings = SETTINGS, bus: EventBus = BUS, wave_id=None,
                  metrics: dict | None = None, shadow_split=None) -> WaveResult:
    """Allocate paid orders to sites and bins and group them into waves (one per site and service).

    Orders are taken express first, then oldest paid first. Each order is allocated whole from one
    site when any site can do that (the site with the lowest priority value wins, ties by code); else
    it is split across sites line by line; else what's missing is backordered and the rest waits
    (no partial shipments, see SHORT_PICK_TOLERANCE). Within a site, lots are taken first-expiring
    first and a site's safety stock is never allocated.

    shadow_split: optional callable(order, {site: {sku: qty}}) -> (plan, missing), the old engine's
    splitter (core.order_engine.splitForShipping), compared and logged during INC-2418."""
    t0 = time.perf_counter()
    now = now or datetime.combine(today, datetime.min.time())
    trace = settings.flag("wave_trace")
    stats = Counter()
    result = WaveResult()
    wave_id = wave_id or (lambda site, service, n: f"wv_{site}_{service}_{now:%Y%m%d%H%M}_{n}")
    sites_by_code = {s.code: s for s in sites}
    waves: dict[tuple[str, str], Wave] = {}
    wave_lines: Counter = Counter()
    log.info("INC-2418 allocate_wave start orders=%d sites=%s today=%s", len(orders), [s.code for s in sites], today)

    # --- INC-2418: snapshot of availability before allocation, to detect overselling afterwards ------------
    before: dict[tuple[str, str], int] = {}
    for lot in stock.lots:
        k = (lot.site, lot.sku)
        before[k] = before.get(k, 0) + (lot.qty if (lot.expires is None or lot.expires >= today) else 0)
    demanded = Counter()
    for wo in orders:
        for line in wo.order.lines:
            demanded[line.sku] += line.qty
    for sku, qty in demanded.items():
        supply = sum(v for (site, s), v in before.items() if s == sku)
        if qty > supply + OVERSELL_ALERT_UNITS:
            log.warning("INC-2418 demand for %s (%d) exceeds supply (%d) by more than %d", sku, qty, supply,
                        OVERSELL_ALERT_UNITS)
            stats["oversell_alerts"] += 1
    t_snapshot = time.perf_counter()

    # --- order of processing ---------------------------------------------------------------------------------
    def sort_key(wo: WaveOrder):
        return (0 if wo.service == EXPRESS else 1, wo.paid_at or datetime.max, wo.order.id)
    queue = sorted(orders, key=sort_key)
    # queue = sorted(orders, key=lambda wo: wo.paid_at)     # pre-2025: express orders waited behind old ones

    for wo in queue:
        order = wo.order
        t_order = time.perf_counter()
        if wo.hold:
            result.skipped[order.id] = f"hold: {wo.hold}"
            stats["held"] += 1
            if trace:
                log.info("INC-2418 TRACE order=%s skipped, hold=%s", order.id, wo.hold)
            continue
        if not order.lines:
            result.skipped[order.id] = "no lines"
            stats["empty"] += 1
            continue
        wanted: dict[str, int] = defaultdict(int)
        for line in order.lines:
            wanted[line.sku] += line.qty
        if trace:
            log.info("INC-2418 TRACE order=%s service=%s wanted=%s gift=%s country=%s", order.id, wo.service,
                     dict(wanted), wo.gift, wo.country)

        # --- which sites may ship this order ------------------------------------------------------------------
        # Hazmat (lithium batteries and the like) can only go by ground: an order containing any hazmat
        # SKU is allocated only from sites that have ground service, and it never goes into an express
        # wave (express leaves by air). Such an order is downgraded to a standard wave instead of being
        # refused; customer service emails the customer (event order.service_downgraded).
        has_hazmat = any(sku in hazmat_skus for sku in wanted)
        service = wo.service
        eligible = [s for s in sites if s.country == wo.country or settings.flag("cross_border_fulfillment")]
        if not eligible:
            eligible = list(sites)            # INC-2418 hotfix: never strand an order because of the country filter
            stats["country_fallback"] += 1
        if has_hazmat:
            ground = [s for s in eligible if "ground" in s.services]
            if len(ground) < len(eligible):
                log.info("INC-2418 order %s has hazmat, %d of %d sites excluded", order.id,
                         len(eligible) - len(ground), len(eligible))
            eligible = ground
            if service == EXPRESS:
                service = STANDARD
                stats["hazmat_downgraded"] += 1
                bus.publish("order.service_downgraded", order_id=order.id, reason="hazmat", to=STANDARD)
        if not eligible:
            result.skipped[order.id] = "no eligible site"
            stats["no_site"] += 1
            log.warning("INC-2418 order %s: no eligible site (hazmat=%s, country=%s)", order.id, has_hazmat, wo.country)
            continue
        eligible.sort(key=lambda s: (s.priority, s.code))

        # --- availability per eligible site, minus safety stock -----------------------------------------------
        avail: dict[str, dict[str, int]] = {}
        for s in eligible:
            avail[s.code] = {sku: max(stock.available(s.code, sku, today) - s.safety_stock, 0) for sku in wanted}
        if trace:
            log.info("INC-2418 TRACE order=%s availability=%s", order.id, json.dumps(avail, sort_keys=True))

        # --- single site if possible ------------------------------------------------------------------------------
        plan: dict[str, dict[str, int]] = {}
        whole = [code for code in avail if all(avail[code][sku] >= q for sku, q in wanted.items())]
        if whole:
            plan[whole[0]] = dict(wanted)
            stats["single_site"] += 1
        elif wo.gift and not settings.flag("split_gift_orders"):
            # gift orders arrive as one parcel or not at all (the gift message prints on the first parcel only)
            result.backordered[order.id] = {sku: q - max(avail[c][sku] for c in avail) for sku, q in wanted.items()
                                            if max(avail[c][sku] for c in avail) < q}
            if not result.backordered[order.id]:
                result.backordered[order.id] = {"__split_needed__": 1}
            stats["gift_waiting"] += 1
            continue
        else:
            remaining = dict(wanted)
            for code in avail:
                for sku in list(remaining):
                    take = min(avail[code][sku], remaining[sku])
                    if take > 0:
                        plan.setdefault(code, {})[sku] = plan.get(code, {}).get(sku, 0) + take
                        remaining[sku] -= take
                        if remaining[sku] == 0:
                            del remaining[sku]
            missing = {sku: q for sku, q in remaining.items() if q > 0}
            if sum(missing.values()) > SHORT_PICK_TOLERANCE:
                result.backordered[order.id] = missing
                stats["backordered"] += 1
                if trace:
                    log.info("INC-2418 TRACE order=%s backordered %s", order.id, missing)
                continue
            stats["split"] += 1
            if shadow_split is not None:
                try:
                    old_plan, old_missing = shadow_split(order, avail)
                    old_sites = sorted(old_plan) if isinstance(old_plan, dict) else []
                    if old_sites != sorted(plan):
                        log.info("INC-2418 shadow split differs for %s: new %s old %s", order.id, sorted(plan), old_sites)
                        stats["shadow_diff"] += 1
                except Exception as e:                       # the shadow must never break allocation
                    log.warning("INC-2418 shadow split failed for %s: %s", order.id, e)
                    stats["shadow_error"] += 1

        # --- bins and lots (FEFO), wave assignment ------------------------------------------------------------
        for code, skus in plan.items():
            key = (code, service)
            wave = waves.get(key)
            if wave is None or len(wave.orders) >= MAX_WAVE_ORDERS or wave_lines[key] >= MAX_WAVE_LINES:
                n = sum(1 for k in waves if k[0] == code and k[1] == service) + 1
                wave = Wave(wave_id(code, service, n), code, service, now)
                waves[key] = wave
                result.waves.append(wave)
                wave_lines[key] = 0
            for sku, qty in skus.items():
                left = qty
                for lot in stock.fefo(code, sku, today):
                    if left == 0:
                        break
                    take = min(lot.qty, left)
                    stock.take(lot, take)
                    wave.allocations.append(Allocation(order.id, sku, code, lot.bin, take, lot.expires))
                    wave_lines[key] += 1
                    left -= take
                if left:
                    # can't happen unless stock changed between the availability check and here
                    log.error("INC-2418 lost %d units of %s at %s for order %s", left, sku, code, order.id)
                    stats["lost_units"] += left
            if order.id not in wave.orders:
                wave.orders.append(order.id)
        stats["allocated_orders"] += 1
        if (time.perf_counter() - t_order) > 0.2:
            log.warning("INC-2418 slow allocation for %s: %.0f ms", order.id, (time.perf_counter() - t_order) * 1000)

    # --- INC-2418: oversell check after allocation ------------------------------------------------------------
    after: dict[tuple[str, str], int] = {}
    for lot in stock.lots:
        k = (lot.site, lot.sku)
        after[k] = after.get(k, 0) + lot.qty
        if lot.qty < 0:
            log.error("INC-2418 negative lot %s/%s at %s: %d", lot.site, lot.sku, lot.bin, lot.qty)
            stats["negative_lots"] += 1
    allocated_units = sum(a.qty for w in result.waves for a in w.allocations)
    consumed = sum(before.values()) - sum(v for k, v in after.items() if k in before)
    if consumed != allocated_units:
        log.error("INC-2418 allocated %d units but stock dropped by %d", allocated_units, consumed)
        stats["unit_mismatch"] += 1
    for s in sites:
        if s.safety_stock:
            for (code, sku), v in after.items():
                if code == s.code and before.get((code, sku), 0) >= s.safety_stock and v < s.safety_stock:
                    log.error("INC-2418 safety stock breached %s/%s: %d < %d", code, sku, v, s.safety_stock)
                    stats["safety_breach"] += 1

    # --- events, metrics --------------------------------------------------------------------------------------
    for w in result.waves:
        bus.publish("wave.created", wave_id=w.id, site=w.site, service=w.service, orders=len(w.orders),
                    lines=len(w.allocations))
    for oid, missing in result.backordered.items():
        bus.publish("order.backordered", order_id=oid, missing=missing)
    stats["waves"] = len(result.waves)
    stats["total_ms"] = (time.perf_counter() - t0) * 1000
    stats["snapshot_ms"] = (t_snapshot - t0) * 1000
    result.stats = dict(stats)
    if metrics is not None:
        for k, v in result.stats.items():
            metrics["waves." + k] = metrics.get("waves." + k, 0) + v
    log.info("INC-2418 allocate_wave done %s", json.dumps(result.stats, sort_keys=True))
    return result


def release(wave: Wave, bus: EventBus = BUS) -> dict:
    """Mark a wave released and build the WMS payload (one pick line per allocation)."""
    if wave.released:
        raise ValueError(f"wave {wave.id} already released")
    wave.released = True
    payload = {"wave": wave.id, "site": wave.site, "service": wave.service,
               "picks": [{"order": a.order_id, "sku": a.sku, "bin": a.bin, "qty": a.qty} for a in
                         sorted(wave.allocations, key=lambda a: (a.bin, a.sku, a.order_id))]}
    bus.publish("wave.released", wave_id=wave.id, picks=len(payload["picks"]))
    return payload


def pick_list_text(wave: Wave) -> str:
    """Printable pick list, sorted by bin so the picker walks the aisles once."""
    rows = [f"Wave {wave.id} ({wave.site}, {wave.service})", ""]
    for a in sorted(wave.allocations, key=lambda a: (a.bin, a.sku)):
        rows.append(f"{a.bin:<10} {a.sku:<14} x{a.qty:<4} order {a.order_id}")
    rows.append("")
    rows.append(f"{len(wave.orders)} orders, {sum(a.qty for a in wave.allocations)} units")
    return "\n".join(rows)
