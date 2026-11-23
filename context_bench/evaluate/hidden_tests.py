"""Run a task's hidden tests against the patched sandbox."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REUSE_TEST = "test_reuse.py"


def run_hidden_tests(sandbox: Path, tests_dir: Path, shared_dir: Path,
                     timeout: int = 120) -> tuple[int, int, bool | None, str]:
    """Return (passed, total, reuse_passed, output).

    passed/total count the correctness tests only; test_reuse.py, if present, is run separately and
    gives reuse_passed (None if the task has no reuse test). The sandbox and shared_dir
    (tasks/_shared, for reuse_spy) are on PYTHONPATH; tests live outside the sandbox.

    A collection error (e.g. the function under test doesn't exist) counts as one failed test.
    total == 0 means the tests could not run at all (timeout); the runner labels that broken."""
    reuse_file = tests_dir / REUSE_TEST
    passed, total, out = _pytest(sandbox, shared_dir, timeout, [str(tests_dir), f"--ignore={reuse_file}"])
    reuse_passed = None
    if reuse_file.exists():
        r_passed, r_total, r_out = _pytest(sandbox, shared_dir, timeout, [str(reuse_file)])
        reuse_passed = r_total > 0 and r_passed == r_total
        out += "\n--- reuse test ---\n" + r_out
    return passed, total, reuse_passed, out


def run_repo_tests(sandbox: Path, shared_dir: Path, timeout: int = 120) -> tuple[int, int, str]:
    """The repository's own test suite (sandbox/tests, as patched) after the change: (passed, total,
    output). Catches patches that pass the hidden tests while breaking or deleting code elsewhere.
    (0, 0, "") when the repo has no tests/ directory."""
    tests = sandbox / "tests"
    if not tests.is_dir():
        return 0, 0, ""
    return _pytest(sandbox, shared_dir, timeout, [str(tests)])


def _pytest(sandbox: Path, shared_dir: Path, timeout: int, args: list[str],
            extra_env: dict[str, str] | None = None) -> tuple[int, int, str]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(sandbox), str(shared_dir)]),
           "PYTHONDONTWRITEBYTECODE": "1", **(extra_env or {})}
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.xml"
        empty_ini = Path(tmp) / "pytest.ini"
        empty_ini.write_text("[pytest]\n")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", str(empty_ini),
               "--rootdir", str(sandbox), f"--junitxml={report}", *args]
        try:
            proc = subprocess.run(cmd, cwd=sandbox, env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return 0, 0, f"TIMEOUT after {timeout}s"
        output = proc.stdout + proc.stderr
        if not report.exists():
            return 0, 0, output
        total = failed = 0
        for suite in ET.parse(report).getroot().iter("testsuite"):
            total += int(suite.get("tests", 0))
            failed += sum(int(suite.get(k, 0)) for k in ("failures", "errors", "skipped"))
        return total - failed, total, output
