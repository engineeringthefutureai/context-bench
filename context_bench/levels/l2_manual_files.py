"""L2 — manual files. Target file + the files a developer would add by hand.

Models the early "@file / /add" workflow. Deterministic proxy for "what a developer would add":
the target's direct imports (resolved to repo files, relative imports included) plus its test file
(tests/test_<stem>.py) if one exists. Nothing else.

Expected behavior: works only when the needed helper's module is already imported by the target —
i.e. when the human already knew where the helper lives.
"""
from __future__ import annotations

from ..types import ContextBlock, Task
from ..workspace import Workspace


def build_context(task: Task, ws: Workspace) -> ContextBlock:
    raise NotImplementedError
