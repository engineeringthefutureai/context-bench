"""patch_coverage / tests_added: what the hidden tests verify of a patch (no model calls)."""
import subprocess
from pathlib import Path

from context_bench.apply import apply_files, make_sandbox
from context_bench.evaluate.patch_coverage import changed_statements, patch_coverage
from context_bench.evaluate.patch_coverage import tests_added as count_tests_added
from context_bench.tasks import load_tasks

ROOT = Path(__file__).resolve().parents[1]
TASKS = load_tasks(ROOT / "tasks")
SHARED = ROOT / "tasks" / "_shared"


def _reference_files(task_id, tmp_path):
    """The reference solution as {path: new contents}."""
    fixture = ROOT / "fixtures" / "repo_M"
    box = make_sandbox(fixture, tmp_path / "ref")
    diff = (ROOT / "tasks" / task_id / "solutions" / "reference.diff").read_text()
    subprocess.run(["patch", "-p1", "-s", "--no-backup-if-mismatch"], cwd=box, input=diff, text=True, check=True)
    files = {}
    for p in box.rglob("*.py"):
        rel = p.relative_to(box).as_posix()
        orig = fixture / rel
        if not orig.is_file() or orig.read_text() != p.read_text():
            files[rel] = p.read_text()
    return TASKS[task_id], files


def test_reference_solution_is_mostly_covered_and_dead_code_is_not(tmp_path):
    task, files = _reference_files("reuse-001", tmp_path)
    box = make_sandbox(ROOT / "fixtures" / "repo_M", tmp_path / "box")
    before = apply_files(box, files)
    res = patch_coverage(box, task.hidden_tests, SHARED, before, files)
    assert res["patch_stmts"] > 0 and res["patch_coverage"] >= 0.8
    # the same patch plus a function nobody calls: its body counts against coverage
    target = task.target_file
    files2 = {**files, target: files[target] + "\n\ndef never_called(x):\n    y = x * 2\n    return y + 1\n"}
    box2 = make_sandbox(ROOT / "fixtures" / "repo_M", tmp_path / "box2")
    before2 = apply_files(box2, files2)
    res2 = patch_coverage(box2, task.hidden_tests, SHARED, before2, files2)
    assert res2["patch_stmts"] == res["patch_stmts"] + 2
    assert res2["patch_stmts_covered"] == res["patch_stmts_covered"]


def test_changed_statements_ignore_blank_comment_docstring_and_def_lines():
    before = {"a.py": "x = 1\n"}
    files = {"a.py": 'x = 1\n\n# note\ndef f():\n    """Doc."""\n    return 2\n', "tests/test_a.py": "def test_x(): pass\n"}
    assert changed_statements(before, files) == {"a.py": {6}}


def test_tests_added_counts_new_test_functions_only():
    before = {"tests/test_a.py": "def test_old():\n    pass\n", "a.py": "x = 1\n"}
    files = {"tests/test_a.py": "def test_old():\n    pass\n\ndef test_new():\n    pass\n",
             "shopkit/test_util_helpers.py": "def test_one():\n    pass\n", "a.py": "x = 2\n"}
    assert count_tests_added(before, files) == {"tests_added": 2, "test_files_changed": 2}
