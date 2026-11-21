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


def test_l6_map_signatures_with_and_without_docs():
    from context_bench.levels.l6_repo_map import build_context as l6
    task, ws = TASKS["reuse-002"], WS
    plain, docs = l6(task, ws).text, l6(task, ws, docs=True).text
    assert plain.startswith("File: shopkit/catalog/models.py\n```python")
    assert "  def slug_for_title(title: str) -> str\n" in plain
    assert "  def slug_for_title(title: str) -> str  # URL slug for a product or campaign title." in docs
    assert "    fields: sku: str, qty: int, unit_cents: int" in plain       # dataclass fields shown
    assert "slug_for_title(self.title)" not in plain and "return " not in plain.split("Repository map")[1]
    assert "shopkit/catalog/models.py\n  " not in plain.split("Repository map")[1]   # target not repeated


def test_l5_task_vs_code_retrieves_matching_helper_with_body():
    ctx = LEVELS["L5"](TASKS["reuse-002"], WS)
    assert ctx.retrieved[0] == "shopkit.utils.text.slug_for_title"
    assert 0 < len(ctx.retrieved) <= 8 and ctx.included[1:] == ctx.retrieved
    assert ctx.queries == [TASKS["reuse-002"].prompt]
    assert _helper_visible(TASKS["reuse-002"], ctx)
    target = TASKS["reuse-002"].target_file
    assert ctx.text.startswith(f"File: {target}\n") and ctx.text.count(f"File: {target}\n") == 1
    assert "import re" in ctx.text                        # the helper file's import header


def test_l5_skips_overlapping_chunks_and_unimplemented_variants():
    from context_bench.levels.l5_function_rag import _select
    from context_bench.types import Chunk
    cls = Chunk("a.py", "a.K", "class", 1, 10, "class K", "", "class K: ...")
    meth = Chunk("a.py", "a.K.m", "function", 3, 5, "def m(self)", "", "def m(self): ...")
    other = Chunk("b.py", "b.f", "function", 1, 2, "def f()", "", "def f(): ...")
    by_id = {c.qualname: c for c in (cls, meth, other)}
    assert _select([("a.K.m", 3.0), ("a.K", 2.0), ("b.f", 1.0)], by_id) == [meth, other]
    import pytest
    with pytest.raises(NotImplementedError):
        LEVELS["L5"](TASKS["reuse-002"], WS, retriever="embed")
    with pytest.raises(NotImplementedError):
        LEVELS["L5"](TASKS["reuse-002"], WS, query_mode="task_vs_summary")


def test_l5_model_queries_plans_merges_and_falls_back():
    from context_bench.levels.l5_function_rag import parse_queries, _merge
    assert parse_queries('```json\n["slug rules", "url path", "slug rules"]\n```') == ["slug rules", "url path"]
    assert parse_queries("1. product title slug\n- url helpers\n") == ["product title slug", "url helpers"]
    assert parse_queries('<file path="a.py">\n```python\n') == []
    assert _merge([[("a", 9), ("b", 8)], [("c", 1), ("a", 0.5)]]) == [("a", 9), ("c", 1), ("b", 8)]
    seen = {}
    def planner(task, l1, instruction):
        seen["l1"], seen["instr"] = l1.text, instruction
        return '["slug for title url"]', 100, 20, 1.5
    ctx = LEVELS["L5"](TASKS["reuse-002"], WS, query_mode="model_queries", planner=planner)
    assert seen["l1"].startswith("File: shopkit/catalog/models.py") and "reus" not in seen["instr"].lower()
    assert ctx.queries == ["slug for title url"] and ctx.retrieved[0] == "shopkit.utils.text.slug_for_title"
    assert len(ctx.retrieved) <= 3 and (ctx.extra_tokens_in, ctx.extra_tokens_out, ctx.extra_latency_s) == (100, 20, 1.5)
    assert ctx.transcript[0]["queries"] == ["slug for title url"] and ctx.note is None
    bad = LEVELS["L5"](TASKS["reuse-002"], WS, query_mode="model_queries", planner=lambda *a: ("<file>", 1, 1, 0.1))
    assert bad.queries == [TASKS["reuse-002"].prompt] and "fell back" in bad.note


def test_l4_target_plus_top_files_whole():
    task = TASKS["reuse-002"]
    ctx = LEVELS["L4"](task, WS)
    assert ctx.text.startswith(f"File: {task.target_file}\n") and 0 < len(ctx.retrieved) <= 3
    assert ctx.retrieved[0] == "shopkit/utils/text.py" and task.target_file not in ctx.retrieved
    assert ctx.included == [task.target_file] + ctx.retrieved
    assert WS.files["shopkit/utils/text.py"].rstrip() in ctx.text      # whole file, not a chunk
    assert _helper_visible(task, ctx)
    import pytest
    with pytest.raises(NotImplementedError):
        LEVELS["L4"](task, WS, retriever="embed")       # no embedder handed in by the runner
