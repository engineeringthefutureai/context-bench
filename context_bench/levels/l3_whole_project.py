"""L3 — whole project. Every source file, sorted by path, each with its path.

Tests whether the model finds the helper when everything is there. If it doesn't fit the model's window, the runner records
ctx_fits=false — never truncate here.
"""
from __future__ import annotations

from ..types import ContextBlock, Task
from ..workspace import Workspace


def build_context(task: Task, ws: Workspace) -> ContextBlock:
    paths = sorted(ws.files)
    blocks = [_show(p, ws.files[p]) for p in paths]
    if task.target_file not in ws.files:
        blocks.append(f"File: {task.target_file}\n(This file does not exist yet.)")
    return ContextBlock(text="\n\n".join(blocks), included=paths)


def _show(path: str, source: str) -> str:
    return f"File: {path}\n```{_lang(path)}\n{source.rstrip()}\n```"


def _lang(path: str) -> str:
    """Code-fence language for a file: python, or sql / html for repo L's migrations and templates."""
    return {".sql": "sql", ".html": "html"}.get(path[path.rfind("."):], "python")
