"""One module per level. Each is self-contained on purpose: duplication between level modules is
intentional so every strategy can be read and studied on its own. Don't factor out shared helpers."""
from __future__ import annotations

from typing import Callable

from ..types import ContextBlock, Task
from ..workspace import Workspace
from . import (
    l0_no_context, l1_one_file, l2_manual_files, l3_whole_project, l4_file_rag,
    l5_function_rag, l6_repo_map, l7_agentic,
)

# level -> build_context(task, workspace, **variant_options) -> ContextBlock
# L0-L7: eight levels, plus the experimental L8. lx_symbol_graph is deprioritized and deliberately not registered.
LEVELS: dict[str, Callable[..., ContextBlock]] = {
    "L0": l0_no_context.build_context,
    "L1": l1_one_file.build_context,
    "L2": l2_manual_files.build_context,
    "L3": l3_whole_project.build_context,
    "L4": l4_file_rag.build_context,
    "L5": l5_function_rag.build_context,
    "L6": l6_repo_map.build_context,
    "L7": l7_agentic.build_context,
    # L8, experimental (2026-11-23): L7's "edit" agent variant (symbol list, read_symbol only, drop, answers as
    # edit blocks with the edit_blocks prompt arm). Same module as L7 on purpose: it is L7's loop with
    # another reading/answer protocol, registered separately so it never mixes into L7's numbers.
    "L8": l7_agentic.build_context,
}

__all__ = ["LEVELS", "Task", "Workspace", "ContextBlock"]
