"""L2 — manual files. Target file + the files a developer would add by hand.

Models the early "@file / /add" workflow. Deterministic proxy for "what a developer would add":
the target's direct imports (resolved to repo files, relative imports included) plus its test file
(tests/test_<stem>.py) if one exists. Nothing else.

Expected behavior: works only when the needed helper's module is already imported by the target —
i.e. when the human already knew where the helper lives.
"""
from __future__ import annotations

import ast
from pathlib import PurePosixPath

from ..types import ContextBlock, Task
from ..workspace import Workspace


def build_context(task: Task, ws: Workspace) -> ContextBlock:
    target = task.target_file
    if target not in ws.files:
        return ContextBlock(text=f"File: {target}\n(This file does not exist yet.)", included=[target])
    paths = [target]
    for path in _imported_files(target, ws.files[target], ws.files):
        if path not in paths:
            paths.append(path)
    test_file = f"tests/test_{PurePosixPath(target).stem}.py"
    if test_file in ws.files and test_file not in paths:
        paths.append(test_file)
    return ContextBlock(text="\n\n".join(_show(p, ws.files[p]) for p in paths), included=paths)


def _imported_files(path: str, source: str, files: dict[str, str]) -> list[str]:
    """Repo files for the target's direct imports, in source order (absolute and relative)."""
    package = list(PurePosixPath(path).parts[:-1])   # the directory = the package (also for __init__.py)
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found += _module_file(alias.name.split("."), files)
        elif isinstance(node, ast.ImportFrom):
            base = node.module.split(".") if node.module else []
            if node.level:               # `.` = this package, `..` = its parent, ...
                base = package[: len(package) - (node.level - 1)] + base
            for alias in node.names:     # `from pkg import mod` imports a submodule
                sub = _module_file(base + [alias.name], files)
                found += sub or _module_file(base, files)
    return found


def _module_file(parts: list[str], files: dict[str, str]) -> list[str]:
    if not parts:
        return []
    stem = "/".join(parts)
    for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
        if candidate in files:
            return [candidate]
    return []


def _show(path: str, source: str) -> str:
    return f"File: {path}\n```python\n{source.rstrip()}\n```"
