"""Append-only results.jsonl with resume by run_id."""
from __future__ import annotations

from pathlib import Path

from .types import RunResult


def done_run_ids(path: Path) -> set[str]:
    raise NotImplementedError


def append(path: Path, result: RunResult) -> None:
    raise NotImplementedError


def report(path: Path) -> str:
    """Label counts per configuration (reuse tasks), duplication when the helper was seen vs not
    seen, and usage per configuration: mean tokens in/out, max peak_ctx_tokens (and % of the
    window), latency, tool calls."""
    raise NotImplementedError
