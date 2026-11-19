"""Append-only results.jsonl with resume by run_id."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .types import RunResult


def done_run_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["run_id"] for line in path.read_text().splitlines() if line.strip()}


def append(path: Path, result: RunResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(asdict(result)) + "\n")


def report(path: Path) -> str:
    """Label counts and reuse rate per configuration, reuse when the helper was seen vs not
    seen, and usage per configuration: mean tokens in/out, max peak_ctx_tokens (and % of the
    window), latency, tool calls."""
    raise NotImplementedError
