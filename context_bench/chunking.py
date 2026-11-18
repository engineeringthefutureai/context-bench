"""Source -> Chunks (functions/classes, methods included) via tree-sitter."""
from __future__ import annotations

from pathlib import Path

from .types import Chunk


def iter_py_files(repo: Path) -> list[Path]:
    """Sorted .py files, skipping caches and VCS dirs."""
    raise NotImplementedError


def module_name(rel_path: str) -> str:
    """'pkg/sub/mod.py' -> 'pkg.sub.mod'; '__init__.py' maps to the package."""
    raise NotImplementedError


def chunks_from_source(source: str, rel_path: str) -> list[Chunk]:
    raise NotImplementedError


def extract_chunks(repo: Path) -> list[Chunk]:
    raise NotImplementedError


def import_header(source: str) -> str:
    """Leading import block of a file."""
    raise NotImplementedError
