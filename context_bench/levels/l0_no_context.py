"""L0 — no context. Task text only.

Runs on control tasks only: a real edit is impossible without seeing the file. Serves as the
baseline for hallucinated helpers (calls to project functions that don't exist).
"""
from __future__ import annotations

from ..types import ContextBlock, Task
from ..workspace import Workspace


def build_context(task: Task, ws: Workspace) -> ContextBlock:
    raise NotImplementedError
