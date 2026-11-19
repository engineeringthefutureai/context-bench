"""Run a task's hidden tests against the patched sandbox."""
from __future__ import annotations

from pathlib import Path


def run_hidden_tests(sandbox: Path, tests_dir: Path, shared_dir: Path,
                     timeout: int = 120) -> tuple[int, int, bool | None, str]:
    """Return (passed, total, reuse_passed, output).

    passed/total count the correctness tests only; test_reuse.py, if present, is run separately and
    gives reuse_passed (None if the task has no reuse test). The sandbox and shared_dir
    (tasks/_shared, for reuse_spy) are on PYTHONPATH; tests live outside the sandbox."""
    raise NotImplementedError
