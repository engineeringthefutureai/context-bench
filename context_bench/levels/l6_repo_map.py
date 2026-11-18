"""L6 — repo map. Target file + one-line signatures of every function and class in the repo,
grouped by file, methods indented under their class. No bodies.

Cheap, global awareness of what exists (aider-style map).
"""
from __future__ import annotations

from ..types import ContextBlock, Task
from ..workspace import Workspace


def build_context(task: Task, ws: Workspace) -> ContextBlock:
    raise NotImplementedError
