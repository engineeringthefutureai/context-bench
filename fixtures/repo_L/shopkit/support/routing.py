"""Routing new tickets to a queue and priority by keywords, order value and customer tier, and macros
(canned replies with placeholders)."""
from __future__ import annotations

import re
from dataclasses import dataclass

from shopkit.support.tickets import Ticket

KEYWORD_QUEUES = [
    (re.compile(r"\b(refund|charged twice|chargeback|money back)\b", re.I), "billing"),
    (re.compile(r"\b(where is|tracking|not arrived|late|delivery)\b", re.I), "shipping"),
    (re.compile(r"\b(broken|damaged|defect|wrong item|missing)\b", re.I), "returns"),
    (re.compile(r"\b(password|log ?in|account|email change)\b", re.I), "accounts"),
    (re.compile(r"\b(invoice|vat|tax|receipt)\b", re.I), "billing"),
]
URGENT_WORDS = re.compile(r"\b(lawyer|legal action|fraud|stolen|police)\b", re.I)


def route(t: Ticket, *, order_total_cents: int = 0, tier: str = "standard") -> Ticket:
    text = t.subject + " " + (t.messages[0].body if t.messages else "")
    for pattern, queue in KEYWORD_QUEUES:
        if pattern.search(text):
            t.queue = queue
            break
    if URGENT_WORDS.search(text):
        t.priority = "urgent"
        t.tags.add("escalation")
    elif tier == "gold" or order_total_cents >= 50_000:
        t.priority = "high"
    return t


@dataclass(frozen=True)
class Macro:
    name: str
    body: str                       # {first_name}, {order_id}, {agent}
    solves: bool = False
    tags: frozenset[str] = frozenset()


MACROS = {
    "where_is_my_order": Macro("where_is_my_order", "Hi {first_name},\n\nYour order {order_id} is on its way. "
                                                    "You can follow it with the tracking link in your shipping email.\n\n{agent}"),
    "refund_done": Macro("refund_done", "Hi {first_name},\n\nWe've refunded order {order_id}. It can take a few "
                                        "business days to show on your statement.\n\n{agent}", solves=True,
                         tags=frozenset({"refunded"})),
    "need_photos": Macro("need_photos", "Hi {first_name},\n\nSorry about that! Could you send us a photo of the "
                                        "item and the packaging?\n\n{agent}"),
}


def render_macro(name: str, *, first_name: str, order_id: str | None, agent: str) -> tuple[str, Macro]:
    macro = MACROS[name]
    body = macro.body.format(first_name=first_name or "there", order_id=order_id or "(your order)", agent=agent)
    return body, macro
