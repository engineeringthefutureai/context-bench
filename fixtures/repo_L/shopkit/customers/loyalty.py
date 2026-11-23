"""Loyalty points. Earned on paid orders, spent at checkout (100 points = $1)."""
from dataclasses import dataclass, field

from shopkit.customers.models import Customer

# points per whole currency unit spent, by customer tier
EARN_RATES = {"standard": 1, "silver": 2, "gold": 3}
POINT_VALUE_CENTS = 1
TIER_THRESHOLDS = [("gold", 50_000), ("silver", 10_000)]   # lifetime spend in cents


def points_for(customer: Customer, spend_cents: int) -> int:
    """Points earned on a purchase: whole units of currency x tier rate. Refunds (negative spend) earn 0."""
    if spend_cents <= 0:
        return 0
    return (spend_cents // 100) * EARN_RATES.get(customer.tier, 1)


def tier_for_lifetime_spend(spend_cents: int) -> str:
    for tier, threshold in TIER_THRESHOLDS:
        if spend_cents >= threshold:
            return tier
    return "standard"


@dataclass
class LoyaltyAccount:
    customer_id: str
    balance: int = 0
    history: list[tuple[str, int]] = field(default_factory=list)   # (reason, delta)

    def earn(self, points: int, reason: str) -> None:
        self.balance += points
        self.history.append((reason, points))

    def redeem(self, points: int, reason: str) -> int:
        """Spend points; returns the discount in cents. Raises ValueError if the balance is short."""
        if points > self.balance:
            raise ValueError(f"balance {self.balance} < {points}")
        self.balance -= points
        self.history.append((reason, -points))
        return points * POINT_VALUE_CENTS
