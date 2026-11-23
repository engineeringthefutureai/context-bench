"""Typed ids: an OrderId can't be passed where a CustomerId is expected."""
from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

_ID = re.compile(r"^[a-z]{2,4}_[0-9a-f]{12}$")


@dataclass(frozen=True)
class TypedId:
    value: str
    prefix: str = ""

    def __post_init__(self):
        if not _ID.match(self.value) or (self.prefix and not self.value.startswith(self.prefix + "_")):
            raise ValueError(f"not a valid {type(self).__name__}: {self.value!r}")

    def __str__(self) -> str:
        return self.value

    @classmethod
    def new(cls):
        return cls(f"{cls.PREFIX}_{secrets.token_hex(6)}", cls.PREFIX)


class OrderId(TypedId):
    PREFIX = "ord"

    def __init__(self, value: str, prefix: str = "ord"):
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "prefix", prefix)
        self.__post_init__()


class CustomerId(TypedId):
    PREFIX = "cus"

    def __init__(self, value: str, prefix: str = "cus"):
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "prefix", prefix)
        self.__post_init__()
