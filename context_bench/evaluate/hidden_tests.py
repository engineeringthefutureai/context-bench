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
        # None when the reuse test couldn't run (the patched code doesn't import): not "not reused"
        reuse_passed = None if reuse_not_run(r_out) else (r_total > 0 and r_passed == r_total)
        out += "\n--- reuse test ---\n" + r_out
    return passed, total, reuse_passed, out


def reuse_not_run(reuse_output: str) -> bool:
    """The reuse test never got to check anything: a collection error (syntax or import error in the
    patched code) or a timeout."""
    return "ERROR collecting" in reuse_output or "error during collection" in reuse_output \
        or reuse_output.startswith("TIMEOUT")


def run_repo_tests(sandbox: Path, shared_dir: Path, timeout: int = 120) -> tuple[int, int, str]:
    """The repository's own test suite (sandbox/tests, as patched) after the change: (passed, total,
    output). Catches patches that pass the hidden tests while breaking or deleting code elsewhere.
    (0, 0, "") when the repo has no tests/ directory. A test module that no longer imports counts as one
    failed test here (its tests can't be collected); `known_tests_broken` names the tests that were lost."""
    passed, collected, out = repo_test_ids(sandbox, shared_dir, timeout)
    return len(passed), len(collected), out


def repo_test_ids(sandbox: Path, shared_dir: Path, timeout: int = 120) -> tuple[set[str], set[str], str]:
    """The repository's own tests after the change, by test id ("tests/test_x.py::test_name"): (passed,
    collected, output). Collection errors don't stop the run (--continue-on-collection-errors): the other
    modules still run, and a module that fails to import appears in `collected` under its own path."""
    tests = sandbox / "tests"
    if not tests.is_dir():
        return set(), set(), ""
    return _pytest_ids(sandbox, shared_dir, timeout, ["--continue-on-collection-errors", str(tests)])


def known_tests_broken(known: set[str], passed: set[str]) -> list[str]:
    """The repository's original tests (`known`: the ids that pass on the unmodified fixture) that don't pass
    after the change: failed, errored, no longer collected (their module doesn't import) or deleted."""
    return sorted(known - passed)


def _pytest_ids(sandbox: Path, shared_dir: Path, timeout: int, args: list[str]) -> tuple[set[str], set[str], str]:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(sandbox), str(shared_dir)]),
           "PYTHONDONTWRITEBYTECODE": "1"}
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.xml"
        empty_ini = Path(tmp) / "pytest.ini"
        empty_ini.write_text("[pytest]\n")
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-c", str(empty_ini),
               "--rootdir", str(sandbox), f"--junitxml={report}", *args]
        try:
            proc = subprocess.run(cmd, cwd=sandbox, env=env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return set(), set(), f"TIMEOUT after {timeout}s"
        output = proc.stdout + proc.stderr
        if not report.exists():
            return set(), set(), output
        passed, collected = set(), set()
        for case in ET.parse(report).getroot().iter("testcase"):
            tid = _test_id(case.get("classname", ""), case.get("name", ""))
            collected.add(tid)
            if not any(child.tag in ("failure", "error", "skipped") for child in case):
                passed.add(tid)
        return passed, collected, output


def _test_id(classname: str, name: str) -> str:
    """junit (classname "tests.test_x[.Class]", name "test_y[param]") -> "tests/test_x.py::[Class::]test_y[param]".
    A collection error has an empty classname and the module ("tests.test_x") as its name."""
    if not classname:
        return name.replace(".", "/") + ".py"
    parts = classname.split(".")
    i = next((k for k in range(len(parts)) if parts[k].startswith("test")), len(parts) - 1)
    if i == 0 and parts[0] == "tests":
        i = 1
    module = "/".join(parts[:i + 1]) + ".py"
    return "::".join([module, *parts[i + 1:], name])


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
