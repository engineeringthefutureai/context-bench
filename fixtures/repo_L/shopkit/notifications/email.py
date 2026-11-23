"""Outgoing email. Messages are queued; a worker hands them to the mail provider.

Plain-text templates come from notifications.templates (the original ones); HTML emails introduced
with the new storefront are rendered from templates/emails/*.html. Every send goes through the
suppression list (hard bounces, complaints, unsubscribes) and a per-recipient hourly limit, so a loop
in a job can't flood one customer.
"""
from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from shopkit.config import SETTINGS
from shopkit.notifications.templates import TEMPLATES, render_template
from shopkit.utils.dates import utc_now
from shopkit.utils.validation import is_valid_email

log = logging.getLogger(__name__)

HOURLY_LIMIT_PER_RECIPIENT = 10
TRANSACTIONAL = {"order_confirmation", "shipped", "refund", "password_reset", "plan_cancelled"}


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    body: str
    reply_to: str = SETTINGS.support_email
    html: str = ""
    category: str = "transactional"


class Suppressed(Exception):
    """The recipient is on the suppression list or over the hourly limit; nothing was queued."""


@dataclass
class SuppressionList:
    reasons: dict[str, str] = field(default_factory=dict)        # email -> "bounce" | "complaint" | "unsubscribe"

    def add(self, email: str, reason: str) -> None:
        self.reasons[email.strip().lower()] = reason

    def blocks(self, email: str, category: str) -> bool:
        """Bounces and complaints block everything; an unsubscribe only blocks marketing."""
        reason = self.reasons.get(email.strip().lower())
        if reason is None:
            return False
        return reason in ("bounce", "complaint") or category == "marketing"


class Outbox:
    def __init__(self, suppression: SuppressionList | None = None):
        self.queued: list[Email] = []
        self.suppression = suppression or SuppressionList()
        self._sent: dict[str, deque[datetime]] = defaultdict(deque)

    def _check(self, to: str, category: str, now: datetime) -> None:
        if not is_valid_email(to):
            raise ValueError(f"invalid recipient {to!r}")
        if self.suppression.blocks(to, category):
            raise Suppressed(f"{to} is suppressed ({self.suppression.reasons.get(to.lower())})")
        window = self._sent[to.lower()]
        while window and now - window[0] > timedelta(hours=1):
            window.popleft()
        if len(window) >= HOURLY_LIMIT_PER_RECIPIENT:
            log.warning("hourly email limit reached for %s", to)
            raise Suppressed(f"{to} got {len(window)} emails in the last hour")
        window.append(now)

    def send(self, to: str, template: str, **values) -> Email:
        category = "transactional" if template in TRANSACTIONAL or template in TEMPLATES else "marketing"
        self._check(to, category, utc_now())
        subject_t, body_t = TEMPLATES[template]
        email = Email(to, render_template(subject_t, values), render_template(body_t, values), category=category)
        self.queued.append(email)
        return email

    def send_html(self, to: str, subject: str, template: str, context: dict, *, category: str = "transactional",
                  now: datetime | None = None) -> Email:
        """HTML email from templates/emails/<template>.html, with a plain-text fallback body."""
        from shopkit.web.templating import render
        self._check(to, category, now or utc_now())
        html = render(f"emails/{template}.html", context)
        text = _html_to_text(html)
        email = Email(to, subject, text, html=html, category=category)
        self.queued.append(email)
        return email

    def drain(self) -> list[Email]:
        out, self.queued = self.queued, []
        return out


def _html_to_text(html: str) -> str:
    """Rough plain-text version for clients that don't show HTML: tags dropped, blank lines collapsed."""
    import re
    text = re.sub(r"<br\s*/?>|</p>|</tr>|</li>", "\n", html)
    text = re.sub(r"<[^>]+>", "", text)
    lines = [" ".join(line.split()) for line in text.splitlines()]
    out, blank = [], False
    for line in lines:
        if not line:
            if not blank and out:
                out.append("")
            blank = True
        else:
            out.append(line)
            blank = False
    return "\n".join(out).strip()
