"""L4 — file-level RAG. Target file + top-3 other files retrieved with the task text as query.

Variants: retriever = "bm25" | "embed" (or any Retriever implementation).
Record the retrieved paths in ContextBlock.retrieved so recall can be computed.

Every file except the target is indexed as file_text (path, then contents), the same text
`cbench validate-retrieval` measures at file level. The target is shown first, then the retrieved
files in rank order, each whole, in the same "File: path" blocks as L1-L3.
"""
from __future__ import annotations

from ..retrieval.base import Retriever, file_text
from ..retrieval.bm25 import BM25Retriever
from ..types import ContextBlock, Task
from ..workspace import Workspace

TOP_FILES = 3
RETRIEVED_HEADER = "Retrieved files (the most relevant other files found by searching the repository):"


def _make_retriever(name: str, ws: Workspace) -> Retriever:
    if name == "bm25":
        return BM25Retriever()
    if name in ws.retrievers:
        return ws.retrievers[name]()
    raise NotImplementedError(f"retriever {name!r}")


def _show(path: str, source: str) -> str:
    return f"File: {path}\n```{_lang(path)}\n{source.rstrip()}\n```"


def build_context(task: Task, ws: Workspace, retriever: str = "bm25") -> ContextBlock:
    path = task.target_file
    if path in ws.files:
        target = _show(path, ws.files[path])
    else:
        target = f"File: {path}\n(This file does not exist yet.)"
    paths = [p for p in sorted(ws.files) if p != path]
    r = _make_retriever(retriever, ws)
    r.index(paths, [file_text(p, ws.files[p]) for p in paths], split_long=True)   # long files: windows
    picked = [p for p, _ in r.search(task.prompt, TOP_FILES)]
    parts = [target] + ([RETRIEVED_HEADER] + [_show(p, ws.files[p]) for p in picked] if picked else [])
    return ContextBlock(text="\n\n".join(parts), included=[path] + picked, retrieved=picked,
                        queries=[task.prompt])


def _lang(path: str) -> str:
    """Code-fence language for a file: python, or sql / html for repo L's migrations and templates."""
    return {".sql": "sql", ".html": "html"}.get(path[path.rfind("."):], "python")
