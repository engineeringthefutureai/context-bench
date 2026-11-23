# -*- coding: utf-8 -*-
#
# OrderEngine - order processing pipeline for the storefront
#
# Written for the first version of the shop (2017). Phone and CSV orders, warehouse (WMS) callbacks,
# order references and totals.
#
# NOTE: amounts in this module are *dollars as float* unless the name ends in Cents. Don't mix.
#
import json
import logging
import re
import time
import traceback
from datetime import datetime, timedelta

log = logging.getLogger("orderengine")

# ---- status codes (int, as stored in the old orders table) -----------------------------------------
STATUS_NEW = 0
STATUS_VALIDATED = 10
STATUS_PAYMENT_PENDING = 20
STATUS_PAID = 30
STATUS_SENT_TO_WMS = 40
STATUS_PICKING = 50
STATUS_PACKED = 60
STATUS_SHIPPED = 70
STATUS_DELIVERED = 80
STATUS_CANCELLED = 90
STATUS_REFUNDED = 95
STATUS_ERROR = 99

STATUS_NAMES = {
    STATUS_NEW: "new", STATUS_VALIDATED: "validated", STATUS_PAYMENT_PENDING: "payment_pending",
    STATUS_PAID: "paid", STATUS_SENT_TO_WMS: "sent_to_wms", STATUS_PICKING: "picking",
    STATUS_PACKED: "packed", STATUS_SHIPPED: "shipped", STATUS_DELIVERED: "delivered",
    STATUS_CANCELLED: "cancelled", STATUS_REFUNDED: "refunded", STATUS_ERROR: "error",
}

# error codes returned by the engine (ok, code) tuples
ERR_NONE = 0
ERR_EMPTY_ORDER = 101
ERR_BAD_QTY = 102
ERR_UNKNOWN_SKU = 103
ERR_OUT_OF_STOCK = 104
ERR_BAD_ADDRESS = 105
ERR_PAYMENT_DECLINED = 201
ERR_PAYMENT_TIMEOUT = 202
ERR_WMS_DOWN = 301
ERR_WMS_REJECTED = 302
ERR_INVALID_STATE = 401
ERR_ALREADY_CANCELLED = 402
ERR_INTERNAL = 999

ERROR_MESSAGES = {
    ERR_EMPTY_ORDER: "Order has no items",
    ERR_BAD_QTY: "Invalid quantity",
    ERR_UNKNOWN_SKU: "Unknown product",
    ERR_OUT_OF_STOCK: "Not enough stock",
    ERR_BAD_ADDRESS: "Shipping address incomplete",
    ERR_PAYMENT_DECLINED: "Payment declined",
    ERR_PAYMENT_TIMEOUT: "Payment provider timeout",
    ERR_WMS_DOWN: "Warehouse system unavailable",
    ERR_WMS_REJECTED: "Warehouse rejected the order",
    ERR_INVALID_STATE: "Order is not in a state that allows this",
    ERR_ALREADY_CANCELLED: "Order already cancelled",
    ERR_INTERNAL: "Internal error",
}

# ---- warehouse (WMS) status codes, as sent in callbacks -------------------------------------------------
WMS_STATUS_RECEIVED = "RCV"
WMS_STATUS_ALLOCATED = "ALC"
WMS_STATUS_PICKING = "PCK"
WMS_STATUS_PACKED = "PKD"
WMS_STATUS_DISPATCHED = "DSP"
WMS_STATUS_SHORT = "SHT"          # short pick: some units not found
WMS_STATUS_CANCELLED = "CNL"
WMS_STATUS_HOLD = "HLD"

# global config - loaded once by loadConfig(); tests poke it directly
CONFIG = {
    "taxRate": 0.0825,                 # default US rate when no address-specific rate is known
    "freeShippingOver": 100.0,
    "flatShipping": 7.95,
    "maxQtyPerLine": 50,
    "wmsUrl": "http://wms.internal/api/orders",
    "wmsTimeout": 10,
    "wmsRetries": 3,
    "paymentTimeout": 30,
    "refPrefix": "SO",
    "allowBackorder": False,
    "b2bDiscount": 0.05,
}

_engineInstance = None
_lastWmsError = None
_retryQueue = []


def loadConfig(path=None, overrides=None):
    """load config from a json file and/or a dict (overrides win)"""
    global CONFIG
    if path:
        try:
            with open(path) as f:
                CONFIG.update(json.load(f))
        except Exception as e:
            log.error("could not load config %s: %s" % (path, e))
    if overrides:
        CONFIG.update(overrides)
    return CONFIG


def getEngine():
    global _engineInstance
    if _engineInstance is None:
        _engineInstance = OrderEngine()
    return _engineInstance


# ---- order references -------------------------------------------------------------------------------
# Sales order refs: SO-2019-000123 (prefix, year, 6-digit sequence). Phone orders: SOB-2020-000045 (B2B).
# New storefront ids: ord_<12 hex> (shopkit.utils.ids.new_id). Both still appear on invoices and in
# customer emails, and support pastes either into the lookup box, often with spaces or lowercase.
_REF_RE = re.compile(r"^(SOB?)-?(\d{4})-?(\d{1,6})$")
_NEW_RE = re.compile(r"^ord_[0-9a-f]{12}$")


def parseOrderRef(ref):
    """Parse an order reference into (kind, year, sequence).

    'SO-2019-000123' -> ('SO', 2019, 123); 'sob 2020 45' -> ('SOB', 2020, 45);
    'ord_1a2b3c4d5e6f' -> ('ord', None, 'ord_1a2b3c4d5e6f'). Returns None when it's neither form.
    Spaces are treated like dashes and case is ignored for the old form; the sequence keeps no
    leading zeros. Years before 2017 are rejected (no orders existed)."""
    if ref is None:
        return None
    s = ref.strip()
    if _NEW_RE.match(s.lower()):
        return ("ord", None, s.lower())
    s = s.upper().replace(" ", "-")
    while "--" in s:
        s = s.replace("--", "-")
    m = _REF_RE.match(s)
    if not m:
        return None
    year = int(m.group(2))
    if year < 2017:
        return None
    return (m.group(1), year, int(m.group(3)))


def formatOrderRef(kind, year, seq):
    if kind == "ord":
        return seq
    return "%s-%04d-%06d" % (kind, int(year), int(seq))


def statusName(code):
    return STATUS_NAMES.get(code, "unknown")


def wmsStatusToShopkit(wmsCode):
    """map a WMS callback status to the storefront status string (shopkit.orders.status).
    Short picks stay 'paid' so ops can decide; holds too. Unknown codes return None."""
    mapping = {
        WMS_STATUS_RECEIVED: "paid",
        WMS_STATUS_ALLOCATED: "paid",
        WMS_STATUS_PICKING: "paid",
        WMS_STATUS_PACKED: "packed",
        WMS_STATUS_DISPATCHED: "shipped",
        WMS_STATUS_SHORT: "paid",
        WMS_STATUS_CANCELLED: "cancelled",
        WMS_STATUS_HOLD: "paid",
    }
    if wmsCode is None:
        return None
    return mapping.get(wmsCode.strip().upper())


def wmsStatusToCode(wmsCode):
    return {
        WMS_STATUS_RECEIVED: STATUS_SENT_TO_WMS,
        WMS_STATUS_ALLOCATED: STATUS_SENT_TO_WMS,
        WMS_STATUS_PICKING: STATUS_PICKING,
        WMS_STATUS_PACKED: STATUS_PACKED,
        WMS_STATUS_DISPATCHED: STATUS_SHIPPED,
        WMS_STATUS_SHORT: STATUS_PICKING,
        WMS_STATUS_CANCELLED: STATUS_CANCELLED,
        WMS_STATUS_HOLD: STATUS_SENT_TO_WMS,
    }.get((wmsCode or "").upper(), STATUS_ERROR)


# ---- money helpers (float dollars) ---------------------------------------------------------------------
def toDollars(cents):
    return round(cents / 100.0, 2)


def toCents(dollars):
    # int(round()) because 19.99*100 = 1998.9999999999998
    return int(round(dollars * 100))


def roundMoney(x):
    return round(x + 0.0000001, 2)


def calcOrderTotals(lines, taxRate=None, shipping=None, discountPct=0.0, b2b=False):
    """Totals for an order. Orders are re-displayed with this (reports must show what the customer
    was charged at the time), so the behaviour must not change, rounding quirks included:

      - subtotal = sum(qty * price) in float dollars
      - B2B orders get CONFIG['b2bDiscount'] off the subtotal *before* any other discount
      - discountPct applies after the B2B discount
      - tax is on (subtotal - discounts), *rounded per order* (not per line)
      - shipping is flat unless subtotal (before discounts!) >= freeShippingOver
      - every intermediate value is rounded to cents with roundMoney

    lines: list of dicts {sku, qty, price} (price in dollars). Returns a dict of floats.
    """
    if taxRate is None:
        taxRate = CONFIG["taxRate"]
    subtotal = 0.0
    for ln in lines:
        subtotal += ln["qty"] * ln["price"]
    subtotal = roundMoney(subtotal)
    b2bDisc = roundMoney(subtotal * CONFIG["b2bDiscount"]) if b2b else 0.0
    disc = roundMoney((subtotal - b2bDisc) * discountPct / 100.0) if discountPct else 0.0
    taxable = roundMoney(subtotal - b2bDisc - disc)
    tax = roundMoney(taxable * taxRate)
    if shipping is None:
        shipping = 0.0 if subtotal >= CONFIG["freeShippingOver"] else CONFIG["flatShipping"]
    total = roundMoney(taxable + tax + shipping)
    return {"subtotal": subtotal, "b2bDiscount": b2bDisc, "discount": disc, "tax": tax,
            "shipping": shipping, "total": total}


class OrderRecord(object):
    """order record as the engine keeps it (dict-like on purpose: the phone-order screen
    serialises it straight to the browser)"""

    def __init__(self, orderId, customerId=None, lines=None, address=None, b2b=False, channel="phone"):
        self.orderId = orderId
        self.customerId = customerId
        self.lines = lines or []          # [{sku, qty, price}] price = dollars
        self.address = address or {}
        self.b2b = b2b
        self.channel = channel
        self.status = STATUS_NEW
        self.totals = {}
        self.paymentRef = None
        self.wmsRef = None
        self.errors = []
        self.history = []
        self.created = datetime.utcnow()
        self.notes = ""
        self.discountPct = 0.0
        self.shortItems = {}

    def setStatus(self, s, why=""):
        self.history.append((datetime.utcnow().isoformat(), self.status, s, why))
        self.status = s

    def toDict(self):
        d = dict(self.__dict__)
        d["created"] = self.created.isoformat()
        d["statusName"] = statusName(self.status)
        return d

    def __getitem__(self, k):
        return getattr(self, k)


class OrderEngine(object):

    def __init__(self, catalog=None, stock=None, payments=None, wms=None, tax=None, mailer=None):
        # catalog: sku -> {price (dollars), active, title}
        self.catalog = catalog if catalog is not None else {}
        # stock: sku -> int available
        self.stock = stock if stock is not None else {}
        self.payments = payments          # object with charge(orderId, amountCents, token) -> (ok, ref|err)
        self.wms = wms                    # object with send(payload) -> (ok, ref|err)
        self.tax = tax                    # callable(address) -> rate, else CONFIG
        self.mailer = mailer              # callable(to, subject, body)
        self.orders = {}
        self.seq = {}
        self.stats = {"processed": 0, "failed": 0, "wmsRetries": 0}

    # ---- numbering -----------------------------------------------------------------------------
    def nextRef(self, b2b=False, year=None):
        year = year or datetime.utcnow().year
        kind = CONFIG["refPrefix"] + ("B" if b2b else "")
        key = (kind, year)
        self.seq[key] = self.seq.get(key, 0) + 1
        return formatOrderRef(kind, year, self.seq[key])

    # ---- validation ----------------------------------------------------------------------------
    def validateOrder(self, order):
        """returns (ok, errCode). Also fills order.errors with human messages"""
        if not order.lines:
            order.errors.append(ERROR_MESSAGES[ERR_EMPTY_ORDER])
            return False, ERR_EMPTY_ORDER
        for ln in order.lines:
            try:
                q = int(ln.get("qty", 0))
            except (TypeError, ValueError):
                q = 0
            if q <= 0 or q > CONFIG["maxQtyPerLine"]:
                order.errors.append("%s: %s" % (ln.get("sku"), ERROR_MESSAGES[ERR_BAD_QTY]))
                return False, ERR_BAD_QTY
            item = self.catalog.get(ln.get("sku"))
            if item is None or not item.get("active", True):
                order.errors.append("%s: %s" % (ln.get("sku"), ERROR_MESSAGES[ERR_UNKNOWN_SKU]))
                return False, ERR_UNKNOWN_SKU
            if not CONFIG["allowBackorder"] and self.stock.get(ln["sku"], 0) < q:
                order.errors.append("%s: %s" % (ln["sku"], ERROR_MESSAGES[ERR_OUT_OF_STOCK]))
                return False, ERR_OUT_OF_STOCK
        a = order.address or {}
        for f in ("name", "line1", "city", "postcode", "country"):
            if not (a.get(f) or "").strip():
                order.errors.append(ERROR_MESSAGES[ERR_BAD_ADDRESS] + " (" + f + ")")
                return False, ERR_BAD_ADDRESS
        return True, ERR_NONE

    def priceOrder(self, order):
        # fill missing prices from the catalog; phone orders may carry a negotiated price
        for ln in order.lines:
            if ln.get("price") is None:
                ln["price"] = self.catalog[ln["sku"]]["price"]
        rate = None
        if self.tax is not None:
            try:
                rate = self.tax(order.address)
            except Exception:
                log.warning("tax lookup failed for %s, using default rate" % order.orderId)
                rate = None
        order.totals = calcOrderTotals(order.lines, rate, None, order.discountPct, order.b2b)
        return order.totals

    # ---- the pipeline ----------------------------------------------------------------------------
    def processOrder(self, order, paymentToken=None, sendToWms=True, notify=True):
        """Run an order through the whole pipeline: validate, price, reserve stock, charge, send to
        the warehouse, email the customer. Returns (ok, errCode).

        On any failure after stock was reserved the reservation is undone. A payment timeout leaves
        the order in PAYMENT_PENDING (the provider may still capture; the reconciler sorts it out).
        A WMS failure after a successful charge does NOT fail the order: it goes to the retry queue
        and the customer still gets the confirmation (ops decision, 2019)."""
        global _lastWmsError
        t0 = time.time()
        self.orders[order.orderId] = order
        try:
            ok, code = self.validateOrder(order)
            if not ok:
                order.setStatus(STATUS_ERROR, "validation: %d" % code)
                self.stats["failed"] += 1
                return False, code
            order.setStatus(STATUS_VALIDATED)

            self.priceOrder(order)
            totalCents = toCents(order.totals["total"])

            # reserve stock
            reserved = []
            for ln in order.lines:
                self.stock[ln["sku"]] = self.stock.get(ln["sku"], 0) - int(ln["qty"])
                reserved.append((ln["sku"], int(ln["qty"])))

            # payment
            if self.payments is not None and totalCents > 0:
                order.setStatus(STATUS_PAYMENT_PENDING)
                try:
                    pok, pres = self.payments.charge(order.orderId, totalCents, paymentToken)
                except TimeoutError:
                    log.warning("payment timeout for %s" % order.orderId)
                    return False, ERR_PAYMENT_TIMEOUT
                except Exception as e:
                    log.error("payment error for %s: %s" % (order.orderId, e))
                    pok, pres = False, str(e)
                if not pok:
                    for sku, q in reserved:
                        self.stock[sku] = self.stock.get(sku, 0) + q
                    order.errors.append(ERROR_MESSAGES[ERR_PAYMENT_DECLINED] + ": " + str(pres))
                    order.setStatus(STATUS_ERROR, "payment declined")
                    self.stats["failed"] += 1
                    return False, ERR_PAYMENT_DECLINED
                order.paymentRef = pres
            order.setStatus(STATUS_PAID)

            # warehouse
            if sendToWms and self.wms is not None:
                payload = self.buildWmsPayload(order)
                sent = False
                for attempt in range(CONFIG["wmsRetries"]):
                    try:
                        wok, wres = self.wms.send(payload)
                    except Exception as e:
                        wok, wres = False, "exception: %s" % e
                    if wok:
                        order.wmsRef = wres
                        order.setStatus(STATUS_SENT_TO_WMS)
                        sent = True
                        break
                    self.stats["wmsRetries"] += 1
                    _lastWmsError = wres
                    if "REJECT" in str(wres).upper():
                        # rejected = no point retrying (bad address format etc)
                        order.errors.append(ERROR_MESSAGES[ERR_WMS_REJECTED] + ": " + str(wres))
                        break
                if not sent:
                    _retryQueue.append((order.orderId, time.time()))
                    log.warning("order %s queued for WMS retry (%s)" % (order.orderId, _lastWmsError))

            # email
            if notify and self.mailer is not None and order.address.get("email"):
                try:
                    self.mailer(order.address["email"], "Your order %s" % order.orderId,
                                self.confirmationText(order))
                except Exception:
                    log.error("confirmation email failed for %s\n%s" % (order.orderId, traceback.format_exc()))

            self.stats["processed"] += 1
            return True, ERR_NONE
        except Exception:
            log.error("processOrder crashed for %s\n%s" % (order.orderId, traceback.format_exc()))
            order.setStatus(STATUS_ERROR, "internal")
            self.stats["failed"] += 1
            return False, ERR_INTERNAL
        finally:
            log.debug("processOrder %s took %.1f ms" % (order.orderId, (time.time() - t0) * 1000))

    def buildWmsPayload(self, order):
        a = order.address
        return {
            "ref": order.orderId,
            "priority": "B2B" if order.b2b else "STD",
            "ship_to": {
                "name": a.get("name", "")[:35],          # WMS label field limit
                "addr1": a.get("line1", "")[:35],
                "addr2": a.get("line2", "")[:35],
                "city": a.get("city", "")[:30],
                "zip": a.get("postcode", "").replace(" ", "").upper(),
                "country": a.get("country", "US").upper(),
            },
            "items": [{"sku": ln["sku"].upper().replace("-", ""), "qty": int(ln["qty"])} for ln in order.lines],
            "notes": (order.notes or "")[:200],
        }

    def confirmationText(self, order):
        out = []
        out.append("Thanks for your order %s." % order.orderId)
        out.append("")
        for ln in order.lines:
            title = self.catalog.get(ln["sku"], {}).get("title", ln["sku"])
            out.append("%3d x %-30s $%8.2f" % (int(ln["qty"]), title[:30], ln["qty"] * ln["price"]))
        t = order.totals
        out.append("")
        out.append("Subtotal: $%.2f" % t.get("subtotal", 0))
        if t.get("b2bDiscount"):
            out.append("Business discount: -$%.2f" % t["b2bDiscount"])
        if t.get("discount"):
            out.append("Discount: -$%.2f" % t["discount"])
        out.append("Tax: $%.2f" % t.get("tax", 0))
        out.append("Shipping: $%.2f" % t.get("shipping", 0))
        out.append("Total: $%.2f" % t.get("total", 0))
        return "\n".join(out)

    # ---- warehouse callbacks (still live) ------------------------------------------------------------
    def handleWmsCallback(self, body, statusBoard=None):
        """The WMS posts {ref, status, items?: [{sku, picked}], tracking?} here. Returns (httpCode, msg).

        Updates the engine's record if we have one and, when a shopkit StatusBoard is passed, moves
        the storefront order too (via wmsStatusToShopkit).
        Short picks record the missing units in order.shortItems and never move the storefront
        status (ops decides). Unknown refs are accepted with 202 so the WMS stops retrying."""
        try:
            ref = (body.get("ref") or "").strip()
            code = (body.get("status") or "").strip().upper()
        except AttributeError:
            return 400, "bad body"
        if not ref or not code:
            return 400, "ref and status required"
        order = self.orders.get(ref)
        if order is not None:
            if code == WMS_STATUS_SHORT:
                for it in body.get("items") or []:
                    try:
                        want = next(int(l["qty"]) for l in order.lines if l["sku"].upper().replace("-", "") == it["sku"])
                        missing = want - int(it.get("picked", 0))
                        if missing > 0:
                            order.shortItems[it["sku"]] = missing
                    except StopIteration:
                        pass
                    except Exception:
                        log.warning("bad short-pick item %r" % (it,))
            order.setStatus(wmsStatusToCode(code), "wms " + code)
            if body.get("tracking"):
                order.notes = (order.notes + " tracking=" + str(body["tracking"])).strip()
        if statusBoard is not None and code != WMS_STATUS_SHORT:
            target = wmsStatusToShopkit(code)
            if target is not None:
                try:
                    current = statusBoard.status(ref)
                    if current != target:
                        statusBoard.move(ref, target)
                except KeyError:
                    return 202, "unknown order, accepted"
                except Exception as e:
                    # invalid transition (e.g. DSP before PKD); WMS sends out of order sometimes
                    log.info("wms callback %s %s ignored: %s" % (ref, code, e))
                    return 200, "ignored"
        if order is None and statusBoard is None:
            return 202, "unknown order, accepted"
        return 200, "ok"

    def retryWms(self, maxAge=86400):
        """resend orders in the retry queue (cron every 10 min). Returns number sent."""
        global _retryQueue
        now = time.time()
        keep, sent = [], 0
        for orderId, queuedAt in _retryQueue:
            order = self.orders.get(orderId)
            if order is None:
                continue
            if now - queuedAt > maxAge:
                log.error("giving up WMS retry for %s after %ds" % (orderId, now - queuedAt))
                order.setStatus(STATUS_ERROR, "wms retry expired")
                continue
            try:
                ok, res = self.wms.send(self.buildWmsPayload(order))
            except Exception as e:
                ok, res = False, str(e)
            if ok:
                order.wmsRef = res
                order.setStatus(STATUS_SENT_TO_WMS, "retry")
                sent += 1
            else:
                keep.append((orderId, queuedAt))
        _retryQueue = keep
        return sent

    # ---- cancellations and refunds ---------------------------------------------------------------------
    def cancelOrder(self, orderId, reason="", restock=True):
        order = self.orders.get(orderId)
        if order is None:
            return False, ERR_INTERNAL
        if order.status == STATUS_CANCELLED:
            return False, ERR_ALREADY_CANCELLED
        if order.status >= STATUS_SHIPPED:
            return False, ERR_INVALID_STATE
        if restock and order.status >= STATUS_VALIDATED and order.status != STATUS_ERROR:
            for ln in order.lines:
                self.stock[ln["sku"]] = self.stock.get(ln["sku"], 0) + int(ln["qty"])
        order.setStatus(STATUS_CANCELLED, reason)
        return True, ERR_NONE

    def refundOrder(self, orderId, amount=None, reason=""):
        """refund (float dollars). amount None = full. Returns (ok, errCode, refundedDollars)"""
        order = self.orders.get(orderId)
        if order is None or order.status not in (STATUS_PAID, STATUS_SENT_TO_WMS, STATUS_SHIPPED,
                                                 STATUS_DELIVERED, STATUS_CANCELLED):
            return False, ERR_INVALID_STATE, 0.0
        total = order.totals.get("total", 0.0)
        already = sum(h[3].startswith("refund ") and float(h[3].split()[1]) or 0.0 for h in order.history
                      if isinstance(h[3], str) and h[3].startswith("refund "))
        if amount is None:
            amount = roundMoney(total - already)
        if amount <= 0 or amount > roundMoney(total - already) + 0.001:
            return False, ERR_INVALID_STATE, 0.0
        order.setStatus(STATUS_REFUNDED if roundMoney(already + amount) >= total else order.status,
                        "refund %.2f %s" % (amount, reason))
        return True, ERR_NONE, amount

    # ---- reporting helpers used by the phone-order screen --------------------------------------------
    def ordersInStatus(self, status):
        return sorted([o for o in self.orders.values() if o.status == status], key=lambda o: o.created)

    def stuckOrders(self, olderThanMinutes=60):
        cutoff = datetime.utcnow() - timedelta(minutes=olderThanMinutes)
        stuck = []
        for o in self.orders.values():
            if o.status in (STATUS_PAYMENT_PENDING, STATUS_PAID) and o.created < cutoff:
                stuck.append(o)
        return stuck

    def dailySummary(self, day=None):
        day = day or datetime.utcnow().date()
        n, revenue, b2b = 0, 0.0, 0
        for o in self.orders.values():
            if o.created.date() != day or o.status in (STATUS_ERROR, STATUS_CANCELLED):
                continue
            n += 1
            revenue += o.totals.get("total", 0.0)
            if o.b2b:
                b2b += 1
        return {"orders": n, "revenue": roundMoney(revenue), "b2b": b2b}


def retryQueueSize():
    return len(_retryQueue)


def clearRetryQueue():
    global _retryQueue
    _retryQueue = []


# ---- B2B phone orders: credit accounts ------------------------------------------------------------------
# Business customers can order on account (net 30). Limits are in dollars.
CREDIT_ACCOUNTS = {}       # customerId -> {"limit": float, "used": float, "onHold": bool, "terms": int}


def setCreditAccount(customerId, limit, terms=30, onHold=False):
    CREDIT_ACCOUNTS[customerId] = {"limit": float(limit), "used": CREDIT_ACCOUNTS.get(customerId, {}).get("used", 0.0),
                                   "onHold": onHold, "terms": terms}


def creditAvailable(customerId):
    acc = CREDIT_ACCOUNTS.get(customerId)
    if acc is None or acc["onHold"]:
        return 0.0
    return roundMoney(acc["limit"] - acc["used"])


def chargeToAccount(customerId, amount):
    """book an order on account; returns (ok, errCode). Over-limit orders are refused unless the
    account has 'overLimitOk' (sales director approval, set by hand in the DB)"""
    acc = CREDIT_ACCOUNTS.get(customerId)
    if acc is None:
        return False, ERR_PAYMENT_DECLINED
    if acc["onHold"]:
        return False, ERR_PAYMENT_DECLINED
    if acc["used"] + amount > acc["limit"] and not acc.get("overLimitOk"):
        return False, ERR_PAYMENT_DECLINED
    acc["used"] = roundMoney(acc["used"] + amount)
    return True, ERR_NONE


def accountStatement(customerId, orders, asOf=None):
    """lines for the monthly statement PDF: (date, ref, amount, dueDate, overdue)"""
    asOf = asOf or datetime.utcnow()
    acc = CREDIT_ACCOUNTS.get(customerId, {"terms": 30})
    rows = []
    for o in sorted(orders, key=lambda x: x.created):
        if o.customerId != customerId or o.status in (STATUS_CANCELLED, STATUS_ERROR):
            continue
        due = o.created + timedelta(days=acc.get("terms", 30))
        rows.append((o.created.strftime("%Y-%m-%d"), o.orderId, o.totals.get("total", 0.0),
                     due.strftime("%Y-%m-%d"), due < asOf))
    return rows


# ---- split shipments --------------------------------------------------------------------------------
def splitForShipping(order, warehouseStock):
    """Split an order across warehouses. warehouseStock: {warehouse: {sku: qty}}. Greedy: the
    warehouse that can ship the most lines in full goes first, then the next one for what's left.
    Lines that no single warehouse has in full are split by quantity across warehouses.
    Returns {warehouse: [{sku, qty}]} and a dict of units nobody has."""
    remaining = dict((ln["sku"], int(ln["qty"])) for ln in order.lines)
    stockLeft = dict((w, dict(s)) for w, s in warehouseStock.items())
    plan = {}
    while remaining:
        best, bestCount = None, 0
        for w, s in stockLeft.items():
            cnt = sum(1 for sku, q in remaining.items() if s.get(sku, 0) >= q)
            if cnt > bestCount:
                best, bestCount = w, cnt
        if best is None:
            break
        for sku in list(remaining):
            q = remaining[sku]
            if stockLeft[best].get(sku, 0) >= q:
                plan.setdefault(best, []).append({"sku": sku, "qty": q})
                stockLeft[best][sku] -= q
                del remaining[sku]
    # partial quantities
    for sku in list(remaining):
        for w in sorted(stockLeft):
            have = stockLeft[w].get(sku, 0)
            if have <= 0:
                continue
            take = min(have, remaining[sku])
            plan.setdefault(w, []).append({"sku": sku, "qty": take})
            stockLeft[w][sku] -= take
            remaining[sku] -= take
            if remaining[sku] == 0:
                del remaining[sku]
                break
    return plan, remaining


# ---- CSV order import (trade shows, EDI fallback) ----------------------------------------------------------
def importOrdersCsv(text, engine=None, b2b=True, dryRun=False):
    """Import orders from the sales team's CSV export.

    Columns: order,customer,sku,qty,price,name,line1,city,postcode,country[,email][,notes]
    One row per line; rows with the same `order` value form one order. Price may be empty (catalog
    price), or "$12.50" / "12,50" (European sales reps). Returns (created, errors) where errors is a
    list of strings. Nothing is processed in dryRun, orders are only parsed and validated."""
    import csv
    import io
    engine = engine or getEngine()
    created, errors = [], []
    byOrder = {}
    try:
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
    except Exception as e:
        return [], ["could not read csv: %s" % e]
    for i, r in enumerate(rows):
        lineNo = i + 2
        try:
            key = (r.get("order") or "").strip()
            if not key:
                errors.append("line %d: no order number" % lineNo)
                continue
            priceTxt = (r.get("price") or "").strip().replace("$", "")
            if "," in priceTxt and "." not in priceTxt:
                priceTxt = priceTxt.replace(",", ".")
            else:
                priceTxt = priceTxt.replace(",", "")
            price = float(priceTxt) if priceTxt else None
            o = byOrder.get(key)
            if o is None:
                o = OrderRecord(key, (r.get("customer") or "").strip() or None, [], {
                    "name": r.get("name", ""), "line1": r.get("line1", ""), "city": r.get("city", ""),
                    "postcode": r.get("postcode", ""), "country": (r.get("country") or "US").upper(),
                    "email": r.get("email", "")}, b2b=b2b, channel="csv")
                o.notes = r.get("notes") or ""
                byOrder[key] = o
            o.lines.append({"sku": (r.get("sku") or "").strip().upper(), "qty": int(r.get("qty") or 0),
                            "price": price})
        except ValueError as e:
            errors.append("line %d: %s" % (lineNo, e))
        except Exception as e:
            errors.append("line %d: unexpected %s" % (lineNo, e))
    for key, o in byOrder.items():
        ok, code = engine.validateOrder(o)
        if not ok:
            errors.append("order %s: %s" % (key, "; ".join(o.errors) or ERROR_MESSAGES.get(code, code)))
            continue
        if dryRun:
            created.append(o)
            continue
        ok, code = engine.processOrder(o, sendToWms=True, notify=False)
        if ok:
            created.append(o)
        else:
            errors.append("order %s failed: %s" % (key, ERROR_MESSAGES.get(code, code)))
    return created, errors


# ---- export to shopkit.orders ----------------------------------------------------------------------
# Open orders in the storefront's format (B2B orders are excluded by the caller).
def exportForMigration(engine, sinceDays=365):
    out = []
    cutoff = datetime.utcnow() - timedelta(days=sinceDays)
    for o in engine.orders.values():
        if o.created < cutoff:
            continue
        out.append({
            "id": o.orderId,
            "customer_id": o.customerId,
            "status": {STATUS_PAID: "paid", STATUS_SENT_TO_WMS: "paid", STATUS_PICKING: "paid",
                       STATUS_PACKED: "packed", STATUS_SHIPPED: "shipped", STATUS_DELIVERED: "delivered",
                       STATUS_CANCELLED: "cancelled", STATUS_REFUNDED: "refunded"}.get(o.status, "pending"),
            "lines": [{"sku": l["sku"], "qty": int(l["qty"]), "unit_cents": toCents(l["price"] or 0)} for l in o.lines],
            "total_cents": toCents(o.totals.get("total", 0.0)),
            "currency": "USD",
            "channel": o.channel,
        })
    return out
