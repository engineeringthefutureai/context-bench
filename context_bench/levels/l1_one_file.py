"""L1 — one file. The target file, with its path.

The chat-paste baseline: what you get by pasting the file you're editing into a chat.
If the target doesn't exist yet, say so explicitly in the context.
"""
from __future__ import annotations

from ..types import ContextBlock, Task
from ..workspace import Workspace


def build_context(task: Task, ws: Workspace) -> ContextBlock:
    path = task.target_file
    if path not in ws.files:
        return ContextBlock(text=f"File: {path}\n(This file does not exist yet.)", included=[path])
    return ContextBlock(text=f"File: {path}\n```{_lang(path)}\n{ws.files[path].rstrip()}\n```", included=[path])


def _lang(path: str) -> str:
    """Code-fence language for a file: python, or sql / html for repo L's migrations and templates."""
    return {".sql": "sql", ".html": "html"}.get(path[path.rfind("."):], "python")
