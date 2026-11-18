"""Model output -> files in a sandbox copy of the repo."""
from __future__ import annotations

from pathlib import Path


class UnsafePath(ValueError):
    """A <file> path that escapes the sandbox."""


def parse_files(text: str) -> dict[str, str]:
    """Extract <file path="..."> blocks; tolerate code fences inside a block."""
    raise NotImplementedError


def make_sandbox(repo: Path, dst: Path) -> Path:
    """Fresh copy of the repo for one run."""
    raise NotImplementedError


def apply_files(sandbox: Path, files: dict[str, str]) -> dict[str, str | None]:
    """Write files; return {rel_path: previous contents or None}. Raise UnsafePath on escape."""
    raise NotImplementedError


def unified_patch(before: dict[str, str | None], files: dict[str, str]) -> str:
    """Unified diff of the change, saved per run for auditing."""
    raise NotImplementedError
