"""L6 — repo map. Target file + one-line signatures of every function and class in the repo,
grouped by file, methods indented under their class. No bodies.

Cheap, global awareness of what exists (aider-style map). Classes list their annotated fields (a
dataclass's fields are its constructor). The target file is shown in full and left out of the map.

Variant docs=True (config `map_docs = true`, label "L6+docs"): each entry also shows the first line
of its docstring. Docstrings help when true and mislead when they've drifted from the code; the
two variants side by side measure which.
"""
from __future__ import annotations

import ast

from ..types import Chunk, ContextBlock, Task
from ..workspace import Workspace

MAP_HEADER = "Repository map (every function and class in the other files; signatures only, no bodies):"
MAP_HEADER_DOCS = ("Repository map (every function and class in the other files; signatures and the "
                   "first docstring line, no bodies):")
MAX_DOC = 100     # characters of the docstring's first line


def _doc(chunk: Chunk) -> str:
    first = chunk.docstring.strip().splitlines()[0] if chunk.docstring.strip() else ""
    if len(first) > MAX_DOC:
        first = first[:MAX_DOC - 1] + "…"
    return f"  # {first}" if first else ""


def _fields(chunk: Chunk) -> str:
    """'id: str, lines: list[OrderLine] = ...' for annotated class-level attributes."""
    try:
        cls = ast.parse(chunk.source.strip()).body[0]
    except (SyntaxError, IndexError):
        return ""
    out = []
    for node in getattr(cls, "body", []):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.append(f"{node.target.id}: {ast.unparse(node.annotation)}" + (" = ..." if node.value else ""))
    return ", ".join(out)


def _map(ws: Workspace, skip: str, docs: bool) -> str:
    by_file: dict[str, list[Chunk]] = {}
    for c in ws.chunks:
        if c.path != skip:
            by_file.setdefault(c.path, []).append(c)
    lines = [MAP_HEADER_DOCS if docs else MAP_HEADER]
    for path in sorted(by_file):
        lines.append(path)
        for c in sorted(by_file[path], key=lambda c: c.start_line):
            depth = c.qualname.count(".") - c.path.removesuffix(".py").removesuffix("/__init__").count("/") - 1
            indent = "  " * (1 + max(depth, 0))
            lines.append(f"{indent}{c.signature}{_doc(c) if docs else ''}")
            if c.kind == "class" and (fields := _fields(c)):
                lines.append(f"{indent}  fields: {fields}")
    return "\n".join(lines)


def build_context(task: Task, ws: Workspace, docs: bool = False) -> ContextBlock:
    path = task.target_file
    if path in ws.files:
        target = f"File: {path}\n```{_lang(path)}\n{ws.files[path].rstrip()}\n```"
    else:
        target = f"File: {path}\n(This file does not exist yet.)"
    return ContextBlock(text=f"{target}\n\n{_map(ws, skip=path, docs=docs)}", included=[path])


def _lang(path: str) -> str:
    """Code-fence language for a file: python, or sql / html for repo L's migrations and templates."""
    return {".sql": "sql", ".html": "html"}.get(path[path.rfind("."):], "python")
