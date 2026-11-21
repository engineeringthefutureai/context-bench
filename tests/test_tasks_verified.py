"""Every task honours its contract (CLAUDE.md, "Fixtures and tasks"), on each repo it runs on:
the unmodified repo fails, the reference solution passes (and reuses, for reuse/trap tasks), a
context-blind solution fails (reuse tasks: fails test_reuse.py), and a trap's trusting solution
fails its tests. Slow-ish (real hidden-test runs), so one test per task and repo."""
import subprocess
from pathlib import Path

import pytest

from context_bench.apply import make_sandbox
from context_bench.evaluate.hidden_tests import run_hidden_tests
from context_bench.tasks import load_tasks

ROOT = Path(__file__).resolve().parents[1]
TASKS = load_tasks(ROOT / "tasks")
SHARED = ROOT / "tasks" / "_shared"
CASES = [(t, size) for t in sorted(TASKS) for size in (TASKS[t].repo_sizes or ("S", "M"))]


def _run(task, size, tmp_path, solution=None):
    box = make_sandbox(ROOT / "fixtures" / f"repo_{size}", tmp_path / (solution or "none"))
    if solution:
        diff = (ROOT / "tasks" / task.id / "solutions" / f"{solution}.diff").read_text()
        subprocess.run(["patch", "-p1", "-s", "--no-backup-if-mismatch"], cwd=box, input=diff, text=True, check=True)
    passed, total, reuse, _ = run_hidden_tests(box, task.hidden_tests, SHARED)
    return passed, total, reuse


@pytest.mark.parametrize("task_id,size", CASES)
def test_task_contract(task_id, size, tmp_path):
    task = TASKS[task_id]
    solutions = {p.stem for p in (ROOT / "tasks" / task_id / "solutions").glob("*.diff")}
    needs_reuse = task.category in ("reuse", "trap")

    passed, total, reuse = _run(task, size, tmp_path)
    assert total == 0 or passed < total or reuse is False, "unmodified repo already passes"

    passed, total, reuse = _run(task, size, tmp_path, "reference")
    assert total > 0 and passed == total, f"reference fails: {passed}/{total}"
    assert reuse is (True if needs_reuse else None), f"reference reuse = {reuse}"

    if "context_blind" in solutions:
        passed, total, reuse = _run(task, size, tmp_path, "context_blind")
        if needs_reuse:                 # trap: own conversion passes its tests but doesn't reuse
            assert reuse is False, "context-blind solution passes the reuse test"
        else:
            assert passed < total, "context-blind solution passes"
    if "trusting" in solutions:
        passed, total, reuse = _run(task, size, tmp_path, "trusting")
        assert passed < total and reuse is True, "trusting solution should reuse and fail"
