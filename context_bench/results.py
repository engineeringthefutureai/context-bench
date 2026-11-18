"""Append-only results.jsonl with resume by run_id."""
from __future__ import annotations

from pathlib import Path

from .types import RunResult


def done_run_ids(path: Path) -> set[str]:
    raise NotImplementedError


def append(path: Path, result: RunResult) -> None:
    raise NotImplementedError


def report(path: Path) -> str:
    """Label counts per configuration (reuse tasks), plus duplication when the helper was seen
    vs not seen."""
    raise NotImplementedError
