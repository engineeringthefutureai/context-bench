"""Outgoing email. Messages are queued; a worker hands them to the mail provider."""
from dataclasses import dataclass

from shopkit.config import SETTINGS
from shopkit.notifications.templates import TEMPLATES, render_template
from shopkit.utils.validation import is_valid_email


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    body: str
    reply_to: str = SETTINGS.support_email


class Outbox:
    def __init__(self):
        self.queued: list[Email] = []

    def send(self, to: str, template: str, **values) -> Email:
        if not is_valid_email(to):
            raise ValueError(f"invalid recipient {to!r}")
        subject_t, body_t = TEMPLATES[template]
        email = Email(to, render_template(subject_t, values), render_template(body_t, values))
        self.queued.append(email)
        return email

    def drain(self) -> list[Email]:
        out, self.queued = self.queued, []
        return out
