"""Small iterable helpers. Kept dependency-free on purpose."""
from collections import defaultdict
from collections.abc import Callable, Hashable, Iterable, Iterator
from typing import TypeVar

T = TypeVar("T")
K = TypeVar("K", bound=Hashable)


def chunked(items: Iterable[T], size: int) -> Iterator[list[T]]:
    """Split into lists of at most `size` items, preserving order. The last chunk may be shorter."""
    if size < 1:
        raise ValueError("size must be >= 1")
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def index_by(items: Iterable[T], key: Callable[[T], K]) -> dict[K, T]:
    """Map key(item) -> item. Raises ValueError on a repeated key instead of silently overwriting."""
    out: dict[K, T] = {}
    for item in items:
        k = key(item)
        if k in out:
            raise ValueError(f"duplicate key {k!r}")
        out[k] = item
    return out


def group_by(items: Iterable[T], key: Callable[[T], K]) -> dict[K, list[T]]:
    groups: dict[K, list[T]] = defaultdict(list)
    for item in items:
        groups[key(item)].append(item)
    return dict(groups)


def first(items: Iterable[T], predicate: Callable[[T], bool], default: T | None = None) -> T | None:
    return next((i for i in items if predicate(i)), default)


def dedupe(items: Iterable[K]) -> list[K]:
    """Remove repeats, keeping the first occurrence's position."""
    return list(dict.fromkeys(items))
