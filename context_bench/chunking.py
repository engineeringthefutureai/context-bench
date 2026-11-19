"""Source -> Chunks (functions/classes, methods included) via the stdlib ast module."""
from __future__ import annotations

from pathlib import Path

from .types import Chunk

_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", ".pytest_cache", ".mypy_cache"}


def iter_py_files(repo: Path) -> list[Path]:
    """Sorted .py files, skipping caches and VCS dirs."""
    return sorted(p for p in repo.rglob("*.py")
                  if not any(part in _SKIP_DIRS or part.endswith(".egg-info")
                             for part in p.relative_to(repo).parts[:-1]))


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
