"""The repo as levels see it: files, chunks, and any indexes a level needs."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .chunking import chunks_from_source, iter_py_files
from .types import Chunk


@dataclass
class Workspace:
    repo: Path
    files: dict[str, str]                                   # repo-relative path -> text
    chunks: list[Chunk]
    indexes: dict[tuple[str, str], Any] = field(default_factory=dict)
    # e.g. ("file", "bm25"), ("func", "embed"), ("summary", "embed") -> Retriever
    summaries: dict[str, str] = field(default_factory=dict)  # qualname -> one-line summary


def load_workspace(repo: Path) -> Workspace:
    """Read every source file and extract chunks. Indexes are added by the levels that need them."""
    files = {p.relative_to(repo).as_posix(): p.read_text() for p in iter_py_files(repo)}
    chunks = [c for path, text in files.items() for c in chunks_from_source(text, path)]
    return Workspace(repo=repo, files=files, chunks=chunks)
