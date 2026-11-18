"""Input generators for behavioral-equivalence checks: one strategy per planted helper,
referenced by name in helpers/registry.toml. Each returns JSON-serializable argument tuples
for the helper's *required* parameters."""
from __future__ import annotations

import random
from typing import Callable

Strategy = Callable[[random.Random], tuple]

STRATEGIES: dict[str, Strategy] = {
    # "sku_strings": ...,
    # "money_cents": ...,
    # "titles": ...,
}


def generate(strategy: str, n: int = 500, seed: int = 0) -> list[tuple]:
    raise NotImplementedError
