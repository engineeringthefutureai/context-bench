"""One module per level. Each is self-contained on purpose: duplication between level modules is
intentional so every strategy can be read and studied on its own. Don't factor out shared helpers."""
from __future__ import annotations

from typing import Callable

from ..types import ContextBlock, Task
from ..workspace import Workspace
from . import (
    l0_no_context, l1_one_file, l2_manual_files, l3_whole_project, l4_file_rag,
    l5_function_rag, l6_repo_map, l7_symbol_graph, l8_agentic,
)

# level -> build_context(task, workspace, **variant_options) -> ContextBlock
LEVELS: dict[str, Callable[..., ContextBlock]] = {
    "L0": l0_no_context.build_context,
    "L1": l1_one_file.build_context,
    "L2": l2_manual_files.build_context,
    "L3": l3_whole_project.build_context,
    "L4": l4_file_rag.build_context,
    "L5": l5_function_rag.build_context,
    "L6": l6_repo_map.build_context,
    "L7": l7_symbol_graph.build_context,
    "L8": l8_agentic.build_context,
}

__all__ = ["LEVELS", "Task", "Workspace", "ContextBlock"]
