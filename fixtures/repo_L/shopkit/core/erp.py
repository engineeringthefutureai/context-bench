# -*- coding: utf-8 -*-
#
# ERP synchronisation (nightly + on demand). Talks to the finance ERP over its XML gateway: item
# numbers, the daily stock file, invoices, product sync.
#
import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime

log = logging.getLogger("erpsync")

ERP_GATEWAY = "https://erp-gw.internal/xml"
ERP_COMPANY = "SHOP01"
ERP_TIMEOUT = 20
ERP_MAX_RETRIES = 4
ERP_SKU_LEN = 12
ERP_WAREHOUSES = {"01": "MAIN", "02": "EAST", "05": "RETURNS"}     # ERP code -> shop warehouse code
SKIP_WAREHOUSES = ("05",)                                          # returns stock isn't sellable

_lastSync = {}
_errors = []


def toErpSku(sku):
    """Shop SKU -> ERP item number. The ERP keeps 12-character upper-case item numbers with no
    separators, zero-padded on the LEFT of the numeric tail: 'MUG-1' -> 'MUG000000001', 'TEA-SET-12'
    -> 'TEASET000012'. Letters stay in front; the digits that end the SKU are padded so the total is
    ERP_SKU_LEN. A SKU without a numeric tail is padded with zeros on the right ('GIFTCARD' ->
    'GIFTCARD0000'). Raises ValueError when it can't fit in 12 characters."""
    s = "".join(c for c in (sku or "").upper() if c.isalnum())
    if not s:
        raise ValueError("empty sku")
    i = len(s)
    while i > 0 and s[i - 1].isdigit():
        i -= 1
    head, tail = s[:i], s[i:]
    if not tail:
        if len(head) > ERP_SKU_LEN:
            raise ValueError("sku too long for ERP: %s" % sku)
        return head.ljust(ERP_SKU_LEN, "0")
    width = ERP_SKU_LEN - len(head)
    if width < len(tail.lstrip("0") or "0"):
        raise ValueError("sku too long for ERP: %s" % sku)
    return head + tail.lstrip("0").rjust(width, "0")


def fromErpSku(itemNo, knownSkus):
    """ERP item number -> shop SKU, by matching against the shop's known SKUs (the mapping is not
    reversible on its own: separators are lost). Returns None when nothing matches."""
    want = (itemNo or "").strip().upper()
    for sku in knownSkus:
        try:
            if toErpSku(sku) == want:
                return sku
        except ValueError:
            continue
    return None


def erpAmount(cents):
    # the ERP wants "1234.50" with exactly two decimals, no thousands separator, dot decimal
    neg = cents < 0
    cents = abs(int(cents))
    return ("-" if neg else "") + "%d.%02d" % (cents // 100, cents % 100)


def parseErpAmount(text):
    t = (text or "0").strip().replace(",", ".")
    neg = t.startswith("-")
    t = t.lstrip("-")
    whole, _, frac = t.partition(".")
    return (-1 if neg else 1) * (int(whole or 0) * 100 + int((frac + "00")[:2]))


def _signature(body, secret):
    return hashlib.sha256((secret + body).encode("utf-8")).hexdigest()


# ---- stock file -------------------------------------------------------------------------------------
def parseStockFile(text, knownSkus):
    """Parse the ERP's fixed-width stock file (05:00 daily).

    Each record: item number (12), warehouse (2), on hand (9, right aligned, may have a trailing '-'
    for negative), blocked (9). Lines starting with '*' are comments; a trailer line 'EOF nnnnnn'
    carries the record count and must match. Blocked units are not sellable: available = on hand -
    blocked, never below 0. Returns ({(warehouse, sku): available}, [warnings])."""
    result, warnings = {}, []
    lines = [l.rstrip("\n") for l in (text or "").splitlines()]
    records = [l for l in lines if l and not l.startswith("*") and not l.startswith("EOF")]
    trailer = [l for l in lines if l.startswith("EOF")]
    if not trailer:
        raise ValueError("stock file has no EOF trailer (truncated transfer?)")
    try:
        expected = int(trailer[-1][3:].strip())
    except ValueError:
        raise ValueError("bad trailer %r" % trailer[-1])
    if expected != len(records):
        raise ValueError("trailer says %d records, file has %d" % (expected, len(records)))
    for n, rec in enumerate(records, 1):
        if len(rec) < 32:
            warnings.append("record %d too short" % n)
            continue
        itemNo, wh = rec[0:12].strip(), rec[12:14]
        onHandTxt, blockedTxt = rec[14:23].strip(), rec[23:32].strip()
        try:
            onHand = -int(onHandTxt[:-1]) if onHandTxt.endswith("-") else int(onHandTxt or 0)
            blocked = int(blockedTxt or 0)
        except ValueError:
            warnings.append("record %d: bad quantity" % n)
            continue
        if wh in SKIP_WAREHOUSES:
            continue
        shopWh = ERP_WAREHOUSES.get(wh)
        if shopWh is None:
            warnings.append("record %d: unknown warehouse %s" % (n, wh))
            continue
        sku = fromErpSku(itemNo, knownSkus)
        if sku is None:
            warnings.append("record %d: unknown item %s" % (n, itemNo))
            continue
        key = (shopWh, sku)
        result[key] = result.get(key, 0) + max(onHand - blocked, 0)
    return result, warnings


# ---- XML messages --------------------------------------------------------------------------------------
def invoiceXml(invoice):
    """invoice: dict {number, date (date), customerNo, currency, lines: [{sku, qty, unitCents, taxCents}],
    shippingCents}. Builds the ERP's INVOICE message."""
    root = ET.Element("Message", {"type": "INVOICE", "company": ERP_COMPANY, "version": "2"})
    h = ET.SubElement(root, "Header")
    ET.SubElement(h, "DocNo").text = invoice["number"]
    ET.SubElement(h, "DocDate").text = invoice["date"].strftime("%Y%m%d")
    ET.SubElement(h, "CustNo").text = invoice.get("customerNo") or "WEBSHOP"
    ET.SubElement(h, "Curr").text = invoice.get("currency", "USD")
    lines = ET.SubElement(root, "Lines")
    total = 0
    for i, ln in enumerate(invoice["lines"], 1):
        l = ET.SubElement(lines, "Line", {"no": str(i)})
        ET.SubElement(l, "Item").text = toErpSku(ln["sku"])
        ET.SubElement(l, "Qty").text = str(int(ln["qty"]))
        ET.SubElement(l, "Price").text = erpAmount(ln["unitCents"])
        ET.SubElement(l, "Tax").text = erpAmount(ln.get("taxCents", 0))
        total += int(ln["qty"]) * ln["unitCents"] + ln.get("taxCents", 0)
    if invoice.get("shippingCents"):
        l = ET.SubElement(lines, "Line", {"no": str(len(invoice["lines"]) + 1)})
        ET.SubElement(l, "Item").text = "FREIGHT00000"
        ET.SubElement(l, "Qty").text = "1"
        ET.SubElement(l, "Price").text = erpAmount(invoice["shippingCents"])
        ET.SubElement(l, "Tax").text = erpAmount(0)
        total += invoice["shippingCents"]
    ET.SubElement(root, "Total").text = erpAmount(total)
    return ET.tostring(root, encoding="unicode")


def parseResponse(xmlText):
    """ERP reply: <Reply status="OK|ERR" ref="..."><Error code="..">text</Error></Reply>"""
    try:
        root = ET.fromstring(xmlText)
    except ET.ParseError as e:
        return False, None, "unparseable reply: %s" % e
    status = root.get("status")
    if status == "OK":
        return True, root.get("ref"), None
    err = root.find("Error")
    return False, None, "%s %s" % (err.get("code") if err is not None else "?", err.text if err is not None else "")


class ErpSync(object):

    def __init__(self, transport=None, secret="", sleep=time.sleep):
        # transport: callable(xmlBody, headers) -> replyXml ; raises on network errors
        self.transport = transport
        self.secret = secret
        self.sleep = sleep
        self.sent = []

    def _post(self, body):
        headers = {"X-Company": ERP_COMPANY, "X-Signature": _signature(body, self.secret)}
        delay = 1.0
        lastErr = None
        for attempt in range(ERP_MAX_RETRIES):
            try:
                reply = self.transport(body, headers)
                ok, ref, err = parseResponse(reply)
                if ok:
                    return True, ref
                if err and err.startswith("E1"):      # E1xx = validation errors, retrying won't help
                    return False, err
                lastErr = err
            except Exception as e:
                lastErr = "transport: %s" % e
            log.warning("ERP post failed (attempt %d): %s" % (attempt + 1, lastErr))
            self.sleep(delay)
            delay = min(delay * 2, 30)
        _errors.append((datetime.utcnow(), lastErr))
        return False, lastErr

    def pushInvoice(self, invoice):
        """send one invoice; returns (ok, refOrError). Already-sent numbers are skipped (ok, 'dup')"""
        if invoice["number"] in self.sent:
            return True, "dup"
        ok, res = self._post(invoiceXml(invoice))
        if ok:
            self.sent.append(invoice["number"])
            _lastSync["invoice"] = datetime.utcnow()
        return ok, res

    def pushInvoices(self, invoices):
        done, failed = [], []
        for inv in invoices:
            try:
                ok, res = self.pushInvoice(inv)
            except Exception as e:
                ok, res = False, str(e)
            (done if ok else failed).append((inv["number"], res))
        return done, failed

    def syncProducts(self, erpItems, products):
        """product sync: erpItems [{item, desc, price (text), active ('Y'/'N')}] into the shop's
        product dict {sku: Product}. Titles ARE overwritten here (unlike catalog.importer)."""
        changed = 0
        skus = list(products)
        for it in erpItems:
            sku = fromErpSku(it.get("item"), skus)
            if sku is None:
                continue
            p = products[sku]
            newPrice = parseErpAmount(it.get("price"))
            newActive = (it.get("active") or "Y").upper() == "Y"
            title = (it.get("desc") or "").strip()
            if p.price_cents != newPrice or p.active != newActive or (title and p.title != title):
                p.price_cents, p.active = newPrice, newActive
                if title:
                    p.title = title
                changed += 1
        _lastSync["products"] = datetime.utcnow()
        return changed


def lastSync():
    return dict(_lastSync)


def recentErrors(n=20):
    return list(_errors[-n:])
