"""Symbol graph (deprioritized, not a level; was L7 until 2026-11-21). Target file + definitions of
the symbols it references + their callers (1 hop), resolved from the code structure (LSP-style,
deterministic, no retrieval).

Not registered in LEVELS. On reuse tasks the needed helper is never referenced from the target
(task design), so this adds little over L2; it pays off on cross-file and convention tasks, worth
revisiting once there are several of those. L7 (agentic) already offers the graph on demand
through find_references.
"""
from __future__ import annotations

from ..types import ContextBlock, Task
from ..workspace import Workspace

MAX_CHUNKS = 15


def build_context(task: Task, ws: Workspace) -> ContextBlock:
    raise NotImplementedError
