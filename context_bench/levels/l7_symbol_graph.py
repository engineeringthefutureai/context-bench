"""L7 — symbol graph. Target file + definitions of the symbols it references + their callers
(1 hop), resolved from the code structure (LSP-style, deterministic, no retrieval).
"""
from __future__ import annotations

from ..types import ContextBlock, Task
from ..workspace import Workspace

MAX_CHUNKS = 15


def build_context(task: Task, ws: Workspace) -> ContextBlock:
    raise NotImplementedError
