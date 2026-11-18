"""Load tasks from tasks/<id>/task.toml; hidden tests sit in tasks/<id>/hidden_tests/."""
from __future__ import annotations

from pathlib import Path

from .types import Task


def load_tasks(root: Path) -> dict[str, Task]:
    raise NotImplementedError
