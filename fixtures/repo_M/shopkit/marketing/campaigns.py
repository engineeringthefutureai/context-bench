"""Marketing campaigns: landing pages and UTM-tagged links for newsletters and ads."""
import re
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlencode

STOREFRONT = "https://shop.example"


def campaign_handle(name: str) -> str:
    """Short handle used as utm_campaign: lowercase, words joined by underscores, max 30 chars.
    "Black Friday 2026!" -> "black_friday_2026"."""
    return "_".join(re.findall(r"[a-z0-9]+", name.lower()))[:30]


@dataclass
class Campaign:
    name: str
    starts: date
    ends: date
    channel: str = "email"                 # email | social | search
    skus: list[str] = field(default_factory=list)

    @property
    def handle(self) -> str:
        return campaign_handle(self.name)

    def link(self, path: str = "/") -> str:
        query = urlencode({"utm_source": self.channel, "utm_medium": self.channel,
                           "utm_campaign": self.handle})
        return f"{STOREFRONT}{path}?{query}"

    def running(self, today: date) -> bool:
        return self.starts <= today <= self.ends
