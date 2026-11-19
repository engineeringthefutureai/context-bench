from pathlib import Path

from context_bench.levels import LEVELS
from context_bench.runner import _helper_visible
from context_bench.tasks import load_tasks
from context_bench.types import Task
from context_bench.workspace import Workspace, load_workspace

ROOT = Path(__file__).resolve().parents[1]
WS = load_workspace(ROOT / "fixtures" / "repo_S")
TASKS = load_tasks(ROOT / "tasks")


def test_l0_is_empty():
    assert LEVELS["L0"](TASKS["control-001"], WS).text == ""


def test_l3_has_every_file_and_the_helpers():
    ctx = LEVELS["L3"](TASKS["reuse-001"], WS)
    assert ctx.included == sorted(WS.files)
    assert all(f"File: {p}\n" in ctx.text for p in WS.files)
    assert _helper_visible(TASKS["reuse-001"], ctx) is True
    assert "(This file does not exist yet.)" in LEVELS["L3"](TASKS["control-001"], WS).text


def test_l2_target_plus_direct_imports():
    ctx = LEVELS["L2"](TASKS["reuse-001"], WS)
    assert ctx.included == ["shopkit/orders/export.py", "shopkit/orders/models.py"]
    assert _helper_visible(TASKS["reuse-001"], ctx) is False


def _task(target):
    return Task(id="t", category="control", prompt="p", target_file=target, needed_helpers=(),
                hidden_tests=Path("."), tier=1)


def test_l2_relative_submodule_and_test_file():
    files = {
        "pkg/__init__.py": "", "pkg/a/__init__.py": "", "pkg/a/target.py":
            "from . import sib\nfrom ..b import thing\nfrom .sub.deep import x\nimport json\nimport pkg.c\n",
        "pkg/a/sib.py": "", "pkg/b.py": "", "pkg/a/sub/deep.py": "", "pkg/c/__init__.py": "",
        "tests/test_target.py": "", "pkg/unrelated.py": "",
    }
    ws = Workspace(repo=Path("."), files=files, chunks=[])
    assert LEVELS["L2"](_task("pkg/a/target.py"), ws).included == [
        "pkg/a/target.py", "pkg/a/sib.py", "pkg/b.py", "pkg/a/sub/deep.py", "pkg/c/__init__.py",
        "tests/test_target.py"]
