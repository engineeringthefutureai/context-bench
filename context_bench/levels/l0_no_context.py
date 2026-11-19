"""L0 — no context. Task text only.

Runs on tier-1 control tasks only: a real edit is impossible without seeing the file. Serves as the
baseline: what the model does with no repo at all.
"""
from __future__ import annotations

from ..types import ContextBlock, Task
from ..workspace import Workspace


def build_context(task: Task, ws: Workspace) -> ContextBlock:
    return ContextBlock(text="")
