import subprocess
from pathlib import Path

import pytest

from context_bench.apply import UnsafePath, apply_files, make_sandbox, parse_files, unified_patch
from context_bench.evaluate.hidden_tests import run_hidden_tests
from context_bench.prompt import REUSE_HINT, SYSTEM_PROMPT, render
from context_bench.tasks import load_tasks
from context_bench.types import ContextBlock

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "repo_S"
TASKS = load_tasks(ROOT / "tasks")


# ---- prompt
def test_render_only_context_and_arm_differ():
    t = TASKS["reuse-001"]
    a = render(t, ContextBlock(text="ctx A"))
    b = render(t, ContextBlock(text="ctx B"))
    assert a[0] == b[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert a[1]["content"].replace("ctx A", "ctx B") == b[1]["content"]
    assert "<context>\nctx A\n</context>" in a[1]["content"]
    assert a[1]["content"].endswith("Target file: shopkit/orders/export.py")
    assert "reus" not in a[0]["content"].lower() + a[1]["content"].lower()


def test_render_empty_context_and_reuse_hint():
    t = TASKS["control-001"]
    base, hint = render(t, ContextBlock(text="")), render(t, ContextBlock(text=""), "reuse_hint")
    assert "<context>\n(none)\n</context>" in base[1]["content"]
    assert hint[0]["content"] == SYSTEM_PROMPT + "\n" + REUSE_HINT and hint[1] == base[1]
    with pytest.raises(ValueError):
        render(t, ContextBlock(text=""), "other")


# ---- apply
def test_parse_files_variants():
    text = ('<think>draft <file path="no.py">x</file></think>Sure!\n'
            '<file path="a.py">\nprint(1)\n</file>\n'
            '<file path="pkg/b.py">\n```python\nx = 2\n```\n</file>\n'
            '<file path="a.py">print(3)</file>')
    assert parse_files(text) == {"a.py": "print(3)\n", "pkg/b.py": "x = 2\n"}
    assert parse_files("no blocks here") == {}


def test_apply_and_patch(tmp_path):
    box = make_sandbox(FIXTURE, tmp_path / "box")
    files = {"shopkit/money.py": "X = 1\n", "shopkit/new/mod.py": "Y = 2\n"}
    before = apply_files(box, files)
    assert before["shopkit/new/mod.py"] is None and "def format_money" in before["shopkit/money.py"]
    assert (box / "shopkit/new/mod.py").read_text() == "Y = 2\n"
    patch = unified_patch(before, files)
    assert "--- a/shopkit/money.py\n+++ b/shopkit/money.py" in patch
    assert "--- /dev/null\n+++ b/shopkit/new/mod.py" in patch
    assert unified_patch({"a.py": "same\n"}, {"a.py": "same\n"}) == ""


@pytest.mark.parametrize("bad", ["../escape.py", "/etc/evil.py", "shopkit/../../escape.py", "."])
def test_apply_rejects_unsafe_paths_without_writing(tmp_path, bad):
    box = make_sandbox(FIXTURE, tmp_path / "box")
    with pytest.raises(UnsafePath):
        apply_files(box, {"shopkit/ok.py": "ok\n", bad: "evil\n"})
    assert not (box / "shopkit/ok.py").exists() and not (tmp_path / "escape.py").exists()


# ---- hidden tests
def _box(tmp_path, task, solution=None):
    box = make_sandbox(FIXTURE, tmp_path / "box")
    if solution:
        diff = (ROOT / "tasks" / task / "solutions" / f"{solution}.diff").read_text()
        subprocess.run(["patch", "-p1", "-s"], cwd=box, input=diff, text=True, check=True)
    return box


@pytest.mark.parametrize("task,solution,expected", [
    ("reuse-001", "reference", (2, 2, True)),
    ("reuse-001", "context_blind", (1, 2, False)),
    ("reuse-002", "reference", (3, 3, True)),
    ("convention-001", "reference", (2, 2, None)),
    ("control-001", None, (0, 1, None)),        # function missing -> collection error = 1 failure
])
def test_run_hidden_tests(tmp_path, task, solution, expected):
    box = _box(tmp_path, task, solution)
    passed, total, reuse, out = run_hidden_tests(box, TASKS[task].hidden_tests, ROOT / "tasks" / "_shared")
    assert (passed, total, reuse) == expected, out


def test_hidden_tests_leave_no_artifacts_in_tasks():
    assert not list((ROOT / "tasks").rglob("__pycache__")) and not list((ROOT / "tasks").rglob(".pytest_cache"))


def test_parse_files_tag_is_case_and_quote_insensitive():
    from context_bench.apply import parse_files
    assert parse_files("<File path='a.py'>\nx = 1\n</File>") == {"a.py": "x = 1\n"}
    assert parse_files('<file  path = "b.py" >\ny = 2\n</FILE>') == {"b.py": "y = 2\n"}
