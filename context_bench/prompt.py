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
    """Return [system, user] chat messages. Empty context renders as "(none)".

    The reuse_hint arm appends REUSE_HINT to the system prompt; nothing else differs between arms."""
    if prompt_arm not in ("base", "reuse_hint"):
        raise ValueError(f"unknown prompt_arm {prompt_arm!r}")
    system = SYSTEM_PROMPT + ("\n" + REUSE_HINT if prompt_arm == "reuse_hint" else "")
    user = USER_TEMPLATE.format(context=context.text.strip() or "(none)", task=task.prompt.strip(),
                                target_file=task.target_file)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]
