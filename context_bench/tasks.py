"""Load tasks from tasks/<id>/task.toml; hidden tests sit in tasks/<id>/hidden_tests/."""
from __future__ import annotations

from pathlib import Path

from .types import Task


def load_tasks(root: Path) -> dict[str, Task]:
    """Raise on a missing or unknown field: category outside Category, tier missing or outside 1..6."""
    raise NotImplementedError
