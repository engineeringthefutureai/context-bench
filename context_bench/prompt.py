"""THE prompt template. Only the <context> block may differ between levels."""
from __future__ import annotations

from .types import ContextBlock, Task

SYSTEM_PROMPT = """You are a software engineer working in an existing Python repository.
Make the change described in the task.
Return the complete new contents of every file you create or modify, each wrapped exactly as:
<file path="relative/path/from/repo/root.py">
...full file contents...
</file>
Return nothing outside the <file> blocks."""

# Ablation arm only (L1 and L5). Never part of the base arm.
REUSE_HINT = "Prefer reusing existing project helpers over writing new ones."

USER_TEMPLATE = """<context>
{context}
</context>

<task>
{task}
</task>

Target file: {target_file}"""


def render(task: Task, context: ContextBlock, prompt_arm: str = "base") -> list[dict]:
    """Return [system, user] chat messages. Empty context renders as "(none)"."""
    raise NotImplementedError
