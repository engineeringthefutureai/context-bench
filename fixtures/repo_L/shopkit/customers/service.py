"""Customer registry used by checkout and the admin API."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import date

from shopkit.customers.models import Address, Customer
from shopkit.errors import Conflict, NotFound, ShopError
from shopkit.events import BUS, EventBus
from shopkit.utils.ids import new_id
from shopkit.utils.validation import is_valid_email

log = logging.getLogger(__name__)

TIER_THRESHOLDS_CENTS = {"gold": 200_000, "silver": 50_000}      # spend in the last 365 days
ERASED_EMAIL_DOMAIN = "erased.invalid"


class CustomerNotFound(NotFound):
    pass


class EmailTaken(Conflict):
    pass


class InvalidEmail(ShopError):
    http_status = 422


class CustomerDirectory:
    def __init__(self, bus: EventBus = BUS):
        self._by_id: dict[str, Customer] = {}
        self._bus = bus
        self._merged_into: dict[str, str] = {}

    def register(self, email: str, name: str, marketing_opt_in: bool = False) -> Customer:
        email = email.strip().lower()
        if not is_valid_email(email):
            raise InvalidEmail(f"invalid email {email!r}", email=email)
        if self.find_by_email(email):
            raise EmailTaken(f"{email} is already registered", email=email)
        customer = Customer(id=new_id("cus"), email=email, name=name.strip(),
                            marketing_opt_in=marketing_opt_in)
        self._by_id[customer.id] = customer
        return customer

    def get(self, customer_id: str) -> Customer:
        customer_id = self._merged_into.get(customer_id, customer_id)
        try:
            return self._by_id[customer_id]
        except KeyError:
            raise CustomerNotFound(f"no customer {customer_id}", customer_id=customer_id) from None

    def find_by_email(self, email: str) -> Customer | None:
        email = email.strip().lower()
        return next((c for c in self._by_id.values() if c.email == email), None)

    def add_address(self, customer_id: str, address: Address, default: bool = False) -> None:
        customer = self.get(customer_id)
        if default:
            customer.addresses.insert(0, address)
        else:
            customer.addresses.append(address)

    def opted_in(self) -> list[Customer]:
        return [c for c in self._by_id.values() if c.marketing_opt_in]

    # ---- account changes (support tooling, 2025) ------------------------------------------------
    def change_email(self, customer_id: str, new_email: str) -> Customer:
        customer = self.get(customer_id)
        new_email = new_email.strip().lower()
        if not is_valid_email(new_email):
            raise InvalidEmail(f"invalid email {new_email!r}", email=new_email)
        other = self.find_by_email(new_email)
        if other and other.id != customer.id:
            raise EmailTaken(f"{new_email} is already registered", email=new_email)
        old = customer.email
        customer.email = new_email
        self._bus.publish("customer.email_changed", customer_id=customer.id, old=old, new=new_email)
        return customer

    def set_opt_in(self, customer_id: str, opted_in: bool, source: str = "account") -> None:
        customer = self.get(customer_id)
        if customer.marketing_opt_in != opted_in:
            customer.marketing_opt_in = opted_in
            self._bus.publish("customer.consent_changed", customer_id=customer.id, opted_in=opted_in, source=source)

    def merge(self, keep_id: str, duplicate_id: str) -> Customer:
        """Merge a duplicate account into `keep_id` (same person registered twice). Addresses and tags
        move over, opt-in is kept only if both had it (consent can't be assumed), and lookups of the
        duplicate id resolve to the kept account from now on."""
        if keep_id == duplicate_id:
            raise ValueError("can't merge an account into itself")
        keep, dup = self.get(keep_id), self.get(duplicate_id)
        for a in dup.addresses:
            if a not in keep.addresses:
                keep.addresses.append(a)
        keep.tags |= dup.tags
        keep.marketing_opt_in = keep.marketing_opt_in and dup.marketing_opt_in
        if dup.joined and (keep.joined is None or dup.joined < keep.joined):
            keep.joined = dup.joined
        del self._by_id[dup.id]
        self._merged_into[dup.id] = keep.id
        self._bus.publish("customer.merged", kept=keep.id, merged=dup.id)
        log.info("merged customer %s into %s", dup.id, keep.id)
        return keep

    def recalculate_tier(self, customer_id: str, spend_last_year_cents: int) -> str:
        """Tier from the last 365 days of spend. Tiers only go down at the yearly review
        (`review_tiers`), so a recalculation never demotes."""
        customer = self.get(customer_id)
        earned = next((t for t, floor in TIER_THRESHOLDS_CENTS.items() if spend_last_year_cents >= floor),
                      "standard")
        order = ["standard", "silver", "gold"]
        if order.index(earned) > order.index(customer.tier):
            customer.tier = earned
            self._bus.publish("customer.tier_changed", customer_id=customer.id, tier=earned)
        return customer.tier

    def review_tiers(self, spend_by_customer: dict[str, int]) -> dict[str, str]:
        """Yearly review: set every customer's tier from spend, up or down. Returns the changes."""
        changes = {}
        for cid, customer in self._by_id.items():
            spend = spend_by_customer.get(cid, 0)
            tier = next((t for t, floor in TIER_THRESHOLDS_CENTS.items() if spend >= floor), "standard")
            if tier != customer.tier:
                changes[cid] = tier
                customer.tier = tier
        return changes

    # ---- privacy requests -------------------------------------------------------------------------
    def export(self, customer_id: str) -> str:
        """Data export for a subject access request: the customer record as JSON."""
        customer = self.get(customer_id)
        data = asdict(customer)
        data["tags"] = sorted(data["tags"])
        data["joined"] = customer.joined.isoformat() if customer.joined else None
        return json.dumps(data, indent=2, sort_keys=True)

    def erase(self, customer_id: str, today: date) -> Customer:
        """Erasure request: personal data removed, the record kept (orders and invoices refer to it)."""
        customer = self.get(customer_id)
        customer.email = f"{customer.id}@{ERASED_EMAIL_DOMAIN}"
        customer.name = "Erased customer"
        customer.addresses.clear()
        customer.marketing_opt_in = False
        customer.tags = {"erased"}
        self._bus.publish("customer.erased", customer_id=customer.id, on=today.isoformat())
        return customer

    def search(self, text: str, limit: int = 20) -> list[Customer]:
        text = text.strip().lower()
        if not text:
            return []
        hits = [c for c in self._by_id.values() if text in c.email or text in c.name.lower()]
        return sorted(hits, key=lambda c: (not c.email.startswith(text), c.email))[:limit]
