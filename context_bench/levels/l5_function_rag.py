"""L5 — function-level RAG. Target file + top-8 functions/classes from other files.

Each retrieved chunk is shown with its file path, the file's import header, signature,
docstring and body.

Variants:
  retriever  = "bm25" | "embed" (or any Retriever implementation)
  query_mode =
    "task_vs_code"      task text searched against function source (naive baseline)
    "task_vs_summary"   task text searched against one-line natural-language summaries of each
                        function (generated once at index time and cached)
    "draft_then_search" the model first drafts the code it would write (with L1 context); the draft
                        is searched against the function index; hits go into context. Draft tokens
                        count toward cost (extra_tokens_in / extra_tokens_out).

Record retrieved qualnames in ContextBlock.retrieved so recall can be computed.
"""
from __future__ import annotations

from typing import Callable

from ..types import ContextBlock, Task
from ..workspace import Workspace

TOP_CHUNKS = 8

# (task, L1-style context) -> (draft_text, tokens_in, tokens_out)
Drafter = Callable[[Task, ContextBlock], tuple[str, int, int]]


def build_context(task: Task, ws: Workspace, retriever: str = "bm25",
                  query_mode: str = "task_vs_code", drafter: Drafter | None = None) -> ContextBlock:
    raise NotImplementedError
