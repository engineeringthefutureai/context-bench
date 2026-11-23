"""Service settings. Defaults suit local development; production overrides come from the environment."""
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Settings:
    default_currency: str = "USD"
    store_country: str = "US"
    low_stock_threshold: int = 5
    invoice_prefix: str = "INV"
    page_size: int = 50
    max_page_size: int = 200
    support_email: str = "support@shopkit.example"
    free_shipping_over_cents: int = 10_000
    supported_currencies: tuple[str, ...] = ("USD", "EUR", "GBP")
    feature_flags: dict[str, bool] = field(default_factory=dict)

    def flag(self, name: str) -> bool:
        return self.feature_flags.get(name, False)


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Build Settings from SHOPKIT_* variables; unknown variables are ignored."""
    env = os.environ if env is None else env
    kwargs: dict[str, object] = {}
    if "SHOPKIT_CURRENCY" in env:
        kwargs["default_currency"] = env["SHOPKIT_CURRENCY"]
    if "SHOPKIT_COUNTRY" in env:
        kwargs["store_country"] = env["SHOPKIT_COUNTRY"]
    if "SHOPKIT_LOW_STOCK" in env:
        kwargs["low_stock_threshold"] = int(env["SHOPKIT_LOW_STOCK"])
    if "SHOPKIT_PAGE_SIZE" in env:
        kwargs["page_size"] = int(env["SHOPKIT_PAGE_SIZE"])
    flags = env.get("SHOPKIT_FLAGS", "")
    kwargs["feature_flags"] = {f.strip(): True for f in flags.split(",") if f.strip()}
    return Settings(**kwargs)


SETTINGS = load_settings({})
