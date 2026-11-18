"""Run a task's hidden tests against the patched sandbox."""
from __future__ import annotations

from pathlib import Path


def run_hidden_tests(sandbox: Path, tests_dir: Path, timeout: int = 120) -> tuple[int, int]:
    """Return (passed, total). The sandbox is on PYTHONPATH; tests live outside it."""
    raise NotImplementedError
