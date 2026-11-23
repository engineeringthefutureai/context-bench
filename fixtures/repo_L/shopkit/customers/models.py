from dataclasses import dataclass, field
from datetime import date


@dataclass
class Address:
    name: str
    line1: str
    city: str
    postcode: str
    country: str                   # ISO 3166-1 alpha-2, e.g. "US", "GB"
    line2: str = ""
    region: str = ""               # state / county


@dataclass
class Customer:
    id: str
    email: str
    name: str
    addresses: list[Address] = field(default_factory=list)
    marketing_opt_in: bool = False
    tier: str = "standard"         # standard | silver | gold
    joined: date | None = None
    tags: set[str] = field(default_factory=set)

    @property
    def default_address(self) -> Address | None:
        return self.addresses[0] if self.addresses else None

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name.strip() else ""
