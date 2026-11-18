"""L4 — file-level RAG. Target file + top-3 other files retrieved with the task text as query.

Variants: retriever = "bm25" | "embed" (or any Retriever implementation).
Record the retrieved paths in ContextBlock.retrieved so recall can be computed.
"""
from __future__ import annotations

from ..types import ContextBlock, Task
from ..workspace import Workspace

TOP_FILES = 3


def build_context(task: Task, ws: Workspace, retriever: str = "bm25") -> ContextBlock:
    raise NotImplementedError
