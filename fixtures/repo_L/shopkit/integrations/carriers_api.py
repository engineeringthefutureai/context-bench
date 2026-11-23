"""Carrier label and tracking APIs (UPS, National Post) behind one interface."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from shopkit.customers.models import Address
from shopkit.errors import ShopError

log = logging.getLogger(__name__)

TRACKING_STATES = {
    "ups": {"M": "label_created", "P": "picked_up", "I": "in_transit", "O": "out_for_delivery", "D": "delivered",
            "X": "exception", "RS": "returned"},
    "post": {"ACCEPTED": "picked_up", "IN TRANSIT": "in_transit", "AT DELIVERY OFFICE": "in_transit",
             "OUT FOR DELIVERY": "out_for_delivery", "DELIVERED": "delivered", "RETURN TO SENDER": "returned",
             "PREADVICE": "label_created"},
}


class CarrierError(ShopError):
    http_status = 502


@dataclass(frozen=True)
class Label:
    carrier: str
    tracking_number: str
    pdf_base64: str


@dataclass
class CarrierClient:
    carrier: str
    transport: object                       # callable(path, body_dict) -> (status, text)
    account: str = ""

    def create_label(self, shipment_id: str, to: Address, weight_g: int, service: str = "ground") -> Label:
        body = {"account": self.account, "reference": shipment_id, "service": service, "weight_g": weight_g,
                "to": {"name": to.name, "line1": to.line1, "line2": to.line2, "city": to.city,
                       "postcode": to.postcode, "country": to.country, "region": to.region}}
        status, text = self.transport("/labels", body)
        if status != 201:
            raise CarrierError(f"{self.carrier} label failed ({status})", carrier=self.carrier, status=status,
                               response=text[:300])
        data = json.loads(text)
        return Label(self.carrier, data["tracking_number"], data["label"])

    def tracking(self, number: str) -> list[tuple[str, str]]:
        """[(iso time, our state)] oldest first. Unknown carrier codes map to "in_transit" (logged)."""
        status, text = self.transport(f"/tracking/{number}", {})
        if status == 404:
            return []
        if status != 200:
            raise CarrierError(f"{self.carrier} tracking failed ({status})", carrier=self.carrier, status=status)
        out = []
        table = TRACKING_STATES.get(self.carrier, {})
        for ev in json.loads(text).get("events", []):
            code = str(ev.get("code", "")).upper() if self.carrier == "post" else str(ev.get("code", ""))
            state = table.get(code)
            if state is None:
                log.info("unknown %s tracking code %r", self.carrier, code)
                state = "in_transit"
            out.append((ev.get("time", ""), state))
        return sorted(out)

    def latest_state(self, number: str) -> str | None:
        events = self.tracking(number)
        return events[-1][1] if events else None
