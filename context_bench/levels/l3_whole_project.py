"""L3 — whole project. Every source file, sorted by path, each with its path.

Tests whether the model finds the helper when everything is there. If it doesn't fit the model's window, the runner records
ctx_fits=false — never truncate here.
"""
from __future__ import annotations

from ..types import ContextBlock, Task
from ..workspace import Workspace


def build_context(task: Task, ws: Workspace) -> ContextBlock:
    raise NotImplementedError
