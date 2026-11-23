"""Delivery worker for outgoing webhooks (api.webhooks queues them; this sends them).

Runs every minute. Each run takes the due deliveries, sends them over HTTP and decides what happens
next: done, retry later, give up (dead letter), or deactivate the endpoint."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from shopkit.config import SETTINGS, Settings
from shopkit.events import BUS, EventBus

log = logging.getLogger(__name__)

RETRY_SCHEDULE = (timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2),
                  timedelta(hours=6), timedelta(hours=12), timedelta(hours=24))
MAX_ATTEMPTS = len(RETRY_SCHEDULE) + 1
BREAKER_FAILURES = 5                       # consecutive failures that open an endpoint's circuit
BREAKER_OPEN_FOR = timedelta(minutes=10)
RATE_LIMIT_PER_MINUTE = 60
SIGNATURE_TOLERANCE_S = 300                # documented for partners: reject signatures older than this
TIMEOUT_S = 10
ALWAYS_DELIVER = {"order.cancelled", "refund.settled"}      # never held back (see the review hold below)

QUEUED, SENDING, DELIVERED, RETRYING, DEAD, HELD = "queued", "sending", "delivered", "retrying", "dead", "held"


@dataclass
class EndpointState:
    id: str
    url: str
    secret: str
    events: set[str]
    api_version: str = "2023-06"           # "2019-01": flat payload; "2023-06": envelope
    active: bool = True
    consecutive_failures: int = 0
    open_until: datetime | None = None
    sent_times: deque = field(default_factory=lambda: deque(maxlen=RATE_LIMIT_PER_MINUTE * 2))
    deactivated_reason: str = ""


@dataclass
class QueuedDelivery:
    id: int
    endpoint_id: str
    event: str
    payload: dict
    created: datetime
    status: str = QUEUED
    attempts: int = 0
    next_attempt_at: datetime | None = None
    last_error: str = ""
    last_status: int | None = None
    order_id: str | None = None
    sequence: int = 0                      # per (endpoint, order): deliveries go out in this order
    history: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class RunReport:
    delivered: list[int] = field(default_factory=list)
    retrying: list[int] = field(default_factory=list)
    dead: list[int] = field(default_factory=list)
    held: list[int] = field(default_factory=list)
    skipped: dict[int, str] = field(default_factory=dict)
    deactivated: list[str] = field(default_factory=list)
    stats: dict[str, float] = field(default_factory=dict)


def _matches(endpoint: EndpointState, event: str) -> bool:
    return "*" in endpoint.events or event in endpoint.events or f"{event.split('.', 1)[0]}.*" in endpoint.events


def shape_payload(delivery: QueuedDelivery, api_version: str) -> dict:
    """Body per API version. 2019-01 partners get the event fields at the top level plus "event";
    2023-06 partners get {"id", "type", "created", "data"}."""
    if api_version == "2019-01":
        body = dict(delivery.payload)
        body["event"] = delivery.event
        return body
    return {"id": f"evt_{delivery.id}", "type": delivery.event, "created": delivery.created.isoformat(),
            "data": delivery.payload}


def signature_header(body: str, secret: str, ts: int) -> str:
    """'t=<unix ts>,v1=<hex hmac of "ts.body">' (the 2023 scheme; partners on 2019-01 get the same)."""
    mac = hmac.new(secret.encode(), f"{ts}.{body}".encode(), hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def deliver_pending(deliveries: list[QueuedDelivery], endpoints: dict[str, EndpointState], transport, *,
                    now: datetime, under_review=None, settings: Settings = SETTINGS, bus: EventBus = BUS,
                    metrics: dict | None = None, max_batch: int = 500, clock=time.time) -> RunReport:
    """Send every delivery that is due. `transport(url, body, headers, timeout)` returns
    (status_code, response_text) or raises on network errors. `under_review(order_id)` says whether an
    order is in fraud review (fraud.review)."""
    t0 = time.perf_counter()
    trace = settings.flag("webhook_trace")
    stats = Counter()
    report = RunReport()
    log.info("INC-2455 deliver_pending start queued=%d endpoints=%d now=%s", len(deliveries), len(endpoints), now)

    # --- pick what's due ----------------------------------------------------------------------------------
    due = [d for d in deliveries if d.status in (QUEUED, RETRYING, HELD)
           and (d.next_attempt_at is None or d.next_attempt_at <= now)]
    due.sort(key=lambda d: (d.next_attempt_at or d.created, d.id))
    if len(due) > max_batch:
        log.warning("INC-2455 %d deliveries due, sending the oldest %d this run", len(due), max_batch)
        stats["deferred_batch_limit"] += len(due) - max_batch
        due = due[:max_batch]
    stats["due"] = len(due)

    # --- INC-2455: duplicate deliveries after the queue table was restored from backup ----------------
    # Same endpoint + event + payload within an hour = duplicate; only the first one goes out.
    seen_fingerprints: dict[str, int] = {}
    for d in sorted(deliveries, key=lambda d: d.id):
        if d.status == DELIVERED and (now - d.created) < timedelta(hours=1):
            fp = hashlib.sha256(f"{d.endpoint_id}|{d.event}|{json.dumps(d.payload, sort_keys=True)}".encode()).hexdigest()
            seen_fingerprints.setdefault(fp, d.id)
    # first version compared ids only, which missed the restored rows (they got new ids):
    # delivered_ids = {d.id for d in deliveries if d.status == DELIVERED}

    # --- per (endpoint, order) ordering: a later event never overtakes an earlier undelivered one -------
    blocked_by: dict[tuple[str, str], int] = {}
    for d in sorted(deliveries, key=lambda d: (d.endpoint_id, d.order_id or "", d.sequence, d.id)):
        if d.order_id and d.status in (QUEUED, RETRYING, HELD, SENDING):
            blocked_by.setdefault((d.endpoint_id, d.order_id), d.sequence)

    t_selected = time.perf_counter()
    for d in due:
        ep = endpoints.get(d.endpoint_id)
        if trace:
            log.info("INC-2455 TRACE delivery=%d endpoint=%s event=%s attempts=%d order=%s seq=%d",
                     d.id, d.endpoint_id, d.event, d.attempts, d.order_id, d.sequence)
        if ep is None:
            d.status = DEAD
            d.last_error = "endpoint deleted"
            report.dead.append(d.id)
            stats["dead_no_endpoint"] += 1
            continue
        if not ep.active:
            report.skipped[d.id] = f"endpoint inactive ({ep.deactivated_reason or 'disabled'})"
            stats["skipped_inactive"] += 1
            continue
        if not _matches(ep, d.event):
            # subscription changed after the delivery was queued: drop it quietly
            d.status = DEAD
            d.last_error = "no longer subscribed"
            report.dead.append(d.id)
            stats["dead_unsubscribed"] += 1
            continue

        # --- fraud review hold ------------------------------------------------------------------------
        # Events about an order that is in fraud review are held back until the review clears, so
        # partners (ERP, 3PL) don't start fulfilling an order we may still cancel. Held deliveries are
        # re-checked every run; their attempts are not counted and they never dead-letter for being
        # held. Cancellations and refunds (ALWAYS_DELIVER) go out regardless: partners must learn
        # that an order is off.
        if d.order_id and under_review is not None and d.event not in ALWAYS_DELIVER:
            try:
                reviewing = bool(under_review(d.order_id))
            except Exception as e:
                reviewing = True                  # fail closed: better late than shipped to a fraudster
                log.warning("INC-2455 review lookup failed for %s (%s), holding", d.order_id, e)
                stats["review_lookup_failed"] += 1
            if reviewing:
                if d.status != HELD:
                    d.history.append((now.isoformat(), "held: order in review"))
                d.status = HELD
                d.next_attempt_at = now + timedelta(minutes=1)
                report.held.append(d.id)
                stats["held_review"] += 1
                continue
            if d.status == HELD:
                d.history.append((now.isoformat(), "review cleared"))
                d.status = QUEUED
                stats["released_review"] += 1

        # --- ordering ---------------------------------------------------------------------------------
        if d.order_id:
            first = blocked_by.get((d.endpoint_id, d.order_id))
            if first is not None and d.sequence > first:
                report.skipped[d.id] = f"waiting for sequence {first}"
                stats["waiting_order"] += 1
                continue

        # --- duplicates ----------------------------------------------------------------------------------
        fp = hashlib.sha256(f"{d.endpoint_id}|{d.event}|{json.dumps(d.payload, sort_keys=True)}".encode()).hexdigest()
        if fp in seen_fingerprints and seen_fingerprints[fp] != d.id:
            d.status = DELIVERED
            d.last_error = f"duplicate of {seen_fingerprints[fp]}"
            d.history.append((now.isoformat(), d.last_error))
            stats["duplicates_suppressed"] += 1
            log.warning("INC-2455 delivery %d is a duplicate of %d, not sent", d.id, seen_fingerprints[fp])
            continue

        # --- circuit breaker ---------------------------------------------------------------------------------
        if ep.open_until and now < ep.open_until:
            d.next_attempt_at = ep.open_until
            report.skipped[d.id] = "circuit open"
            stats["skipped_breaker"] += 1
            continue
        if ep.open_until and now >= ep.open_until:
            log.info("INC-2455 circuit half-open for %s, trying one delivery", ep.id)
            ep.open_until = None

        # --- rate limit ---------------------------------------------------------------------------------------
        cutoff = now - timedelta(minutes=1)
        while ep.sent_times and ep.sent_times[0] < cutoff:
            ep.sent_times.popleft()
        if len(ep.sent_times) >= RATE_LIMIT_PER_MINUTE:
            d.next_attempt_at = now + timedelta(minutes=1)
            report.skipped[d.id] = "rate limited"
            stats["rate_limited"] += 1
            continue

        # --- build and sign ---------------------------------------------------------------------------------
        body = json.dumps(shape_payload(d, ep.api_version), sort_keys=True, separators=(",", ":"), default=str)
        ts = int(clock())
        headers = {"Content-Type": "application/json", "Shopkit-Signature": signature_header(body, ep.secret, ts),
                   "Shopkit-Event": d.event, "Shopkit-Delivery": str(d.id), "Idempotency-Key": f"dlv-{d.id}",
                   "Shopkit-Api-Version": ep.api_version}
        # INC-2455 shadow: the old (pre-2023) signature, logged so partners still verifying it can be found
        if trace:
            old_sig = "sha256=" + hmac.new(ep.secret.encode(), body.encode(), hashlib.sha256).hexdigest()
            log.info("INC-2455 TRACE delivery=%d old_signature=%s new=%s", d.id, old_sig[:20], headers["Shopkit-Signature"][:30])
        # headers["X-Signature"] = sign(body, ep.secret)      # removed 2025-02 after partners migrated

        # --- send --------------------------------------------------------------------------------------------
        d.status = SENDING
        d.attempts += 1
        ep.sent_times.append(now)
        t_send = time.perf_counter()
        try:
            status, text = transport(ep.url, body, headers, TIMEOUT_S)
            error = None
        except Exception as e:                    # timeouts, DNS, TLS: all retried
            status, text, error = None, "", f"{type(e).__name__}: {e}"
        elapsed_ms = (time.perf_counter() - t_send) * 1000
        stats["send_ms_total"] += elapsed_ms
        d.last_status = status
        if trace or elapsed_ms > 5000:
            log.info("INC-2455 delivery=%d endpoint=%s status=%s %.0f ms", d.id, ep.id, status, elapsed_ms)

        # --- classify the response -------------------------------------------------------------------------
        if status is not None and 200 <= status < 300:
            d.status = DELIVERED
            d.last_error = ""
            d.history.append((now.isoformat(), f"delivered {status}"))
            ep.consecutive_failures = 0
            report.delivered.append(d.id)
            seen_fingerprints.setdefault(fp, d.id)
            if d.order_id and blocked_by.get((d.endpoint_id, d.order_id)) == d.sequence:
                del blocked_by[(d.endpoint_id, d.order_id)]
            stats["delivered"] += 1
            continue
        if status == 410:
            # the partner says the endpoint is gone: stop sending to it at all
            ep.active = False
            ep.deactivated_reason = "410 Gone"
            d.status = DEAD
            d.last_error = "endpoint gone (410)"
            report.dead.append(d.id)
            report.deactivated.append(ep.id)
            bus.publish("webhook.endpoint_deactivated", endpoint_id=ep.id, reason="410")
            stats["deactivated"] += 1
            continue
        retryable = status is None or status >= 500 or status in (408, 409, 425, 429)
        d.last_error = error or f"HTTP {status}: {text[:200]}"
        d.history.append((now.isoformat(), d.last_error))
        ep.consecutive_failures += 1
        if ep.consecutive_failures >= BREAKER_FAILURES and ep.open_until is None:
            ep.open_until = now + BREAKER_OPEN_FOR
            log.warning("INC-2455 circuit opened for %s after %d failures", ep.id, ep.consecutive_failures)
            stats["breaker_opened"] += 1
        if not retryable:
            d.status = DEAD
            report.dead.append(d.id)
            stats["dead_4xx"] += 1
            log.info("INC-2455 delivery %d rejected by %s (%s), not retried", d.id, ep.id, status)
            continue
        if d.attempts >= MAX_ATTEMPTS:
            d.status = DEAD
            report.dead.append(d.id)
            stats["dead_attempts"] += 1
            bus.publish("webhook.dead_letter", delivery_id=d.id, endpoint_id=ep.id, event=d.event)
            log.error("INC-2455 delivery %d dead after %d attempts: %s", d.id, d.attempts, d.last_error)
            continue
        delay = RETRY_SCHEDULE[d.attempts - 1]
        if status == 429:
            # honour the partner's Retry-After when it sends one in the body ("retry_after": seconds)
            try:
                delay = max(delay, timedelta(seconds=int(json.loads(text).get("retry_after", 0))))
            except (ValueError, AttributeError, TypeError):
                pass
        d.status = RETRYING
        d.next_attempt_at = now + delay
        report.retrying.append(d.id)
        stats["retrying"] += 1

    # --- wrap up ------------------------------------------------------------------------------------------
    t_end = time.perf_counter()
    stats["select_ms"] = (t_selected - t0) * 1000
    stats["total_ms"] = (t_end - t0) * 1000
    report.stats = dict(stats)
    if metrics is not None:
        for k in ("delivered", "retrying", "held_review", "duplicates_suppressed", "deactivated", "breaker_opened",
                  "rate_limited", "dead_attempts", "dead_4xx"):
            metrics["webhooks." + k] = metrics.get("webhooks." + k, 0) + stats.get(k, 0)
        metrics["webhooks.run_ms"] = round(stats["total_ms"], 1)
        backlog = sum(1 for d in deliveries if d.status in (QUEUED, RETRYING, HELD))
        metrics["webhooks.backlog"] = backlog
        if backlog > 1000:
            log.warning("INC-2455 webhook backlog %d", backlog)
    log.info("INC-2455 deliver_pending done %s", json.dumps(report.stats, sort_keys=True))
    return report


def endpoint_health(endpoints: dict[str, EndpointState], deliveries: list[QueuedDelivery], now: datetime) -> list[dict]:
    """Rows for the partner dashboard: per endpoint, delivered/failed counts in the last 24 hours."""
    since = now - timedelta(hours=24)
    counts: dict[str, Counter] = defaultdict(Counter)
    for d in deliveries:
        if d.created >= since:
            counts[d.endpoint_id][d.status] += 1
    rows = []
    for ep in sorted(endpoints.values(), key=lambda e: e.id):
        c = counts.get(ep.id, Counter())
        total = sum(c.values())
        rows.append({"endpoint": ep.id, "active": ep.active, "open": bool(ep.open_until and now < ep.open_until),
                     "delivered": c[DELIVERED], "failed": c[DEAD] + c[RETRYING], "held": c[HELD],
                     "success_rate": round(c[DELIVERED] / total, 3) if total else None})
    return rows
