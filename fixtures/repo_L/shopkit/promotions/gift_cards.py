"""Gift cards: prepaid balances redeemable at checkout."""
from dataclasses import dataclass, field

from shopkit.errors import Conflict, NotFound
from shopkit.utils.ids import short_code


class GiftCardNotFound(NotFound):
    pass


class InsufficientBalance(Conflict):
    pass


@dataclass
class GiftCard:
    code: str = field(default_factory=lambda: short_code(16, groups=4))
    balance_cents: int = 0
    currency: str = "USD"


class GiftCardStore:
    def __init__(self):
        self._cards: dict[str, GiftCard] = {}

    def issue(self, amount_cents: int, currency: str = "USD") -> GiftCard:
        card = GiftCard(balance_cents=amount_cents, currency=currency)
        self._cards[card.code] = card
        return card

    def get(self, code: str) -> GiftCard:
        try:
            return self._cards[code.strip().upper()]
        except KeyError:
            raise GiftCardNotFound("unknown gift card", code=code) from None

    def redeem(self, code: str, amount_cents: int) -> int:
        """Take up to amount_cents from the card; returns what was taken (may be less if the balance
        is lower, but never zero: an empty card raises)."""
        card = self.get(code)
        if card.balance_cents <= 0:
            raise InsufficientBalance("gift card is empty", code=card.code)
        taken = min(card.balance_cents, amount_cents)
        card.balance_cents -= taken
        return taken
