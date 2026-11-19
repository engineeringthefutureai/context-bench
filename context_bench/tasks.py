"""Load tasks from tasks/<id>/task.toml; hidden tests sit in tasks/<id>/hidden_tests/."""
from __future__ import annotations

import tomllib
from pathlib import Path
from typing import get_args

from .types import Category, Task

_FIELDS = {"id", "category", "tier", "target_file", "needed_helpers", "prompt"}


def load_tasks(root: Path) -> dict[str, Task]:
    """Raise on a missing or unknown field: category outside Category, tier missing or outside 1..6."""
    tasks = {}
    for toml_path in sorted(root.glob("*/task.toml")):
        data = tomllib.loads(toml_path.read_text())
        where = toml_path.relative_to(root)
        if missing := _FIELDS - data.keys():
            raise ValueError(f"{where}: missing {sorted(missing)}")
        if unknown := data.keys() - _FIELDS:
            raise ValueError(f"{where}: unknown {sorted(unknown)}")
        if data["id"] != toml_path.parent.name:
            raise ValueError(f"{where}: id {data['id']!r} doesn't match its directory")
        if data["category"] not in get_args(Category):
            raise ValueError(f"{where}: category {data['category']!r} not in {get_args(Category)}")
        if not isinstance(data["tier"], int) or not 1 <= data["tier"] <= 6:
            raise ValueError(f"{where}: tier must be an integer 1..6, got {data['tier']!r}")
        hidden = toml_path.parent / "hidden_tests"
        if not hidden.is_dir():
            raise ValueError(f"{where}: no hidden_tests/ directory")
        tasks[data["id"]] = Task(
            id=data["id"], category=data["category"], prompt=data["prompt"].strip(),
            target_file=data["target_file"], needed_helpers=tuple(data["needed_helpers"]),
            hidden_tests=hidden, tier=data["tier"],
        )
    return tasks
