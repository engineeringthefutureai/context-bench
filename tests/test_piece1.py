from dataclasses import replace
from pathlib import Path

import pytest

from context_bench.tasks import load_tasks
from context_bench.types import RunConfig
from context_bench.workspace import load_workspace

ROOT = Path(__file__).resolve().parents[1]


def test_run_id_is_stable_and_sensitive():
    rc = RunConfig(task_id="reuse-001", level="L1", model="qwen3.8-27b", seed=1)
    assert rc.run_id == RunConfig(task_id="reuse-001", level="L1", model="qwen3.8-27b", seed=1).run_id
    assert len(rc.run_id) == 16
    for change in ({"level": "L2"}, {"seed": 2}, {"prompt_arm": "reuse_hint"}, {"temperature": 0.2}):
        assert replace(rc, **change).run_id != rc.run_id


def test_load_real_tasks():
    tasks = load_tasks(ROOT / "tasks")
    assert sorted(tasks) == ["control-001", "control-002", "convention-001", "cross-001", "reuse-001", "reuse-002",
                             "trap-001"]
    assert {t.id: t.tier for t in tasks.values()} == {
        "control-001": 1, "control-002": 2, "reuse-002": 3, "reuse-001": 4, "convention-001": 5, "cross-001": 6,
        "trap-001": 4}
    t = tasks["reuse-001"]
    assert t.needed_helpers == ("shopkit.utils.text.normalize_sku", "shopkit.money.format_money")
    assert t.prompt.startswith("Finance wants") and t.hidden_tests.is_dir()


def _write_task(root, **overrides):
    fields = {"id": '"t-1"', "category": '"control"', "tier": "1", "target_file": '"a.py"',
              "needed_helpers": "[]", "prompt": '"do it"', **overrides}
    d = root / "t-1"
    (d / "hidden_tests").mkdir(parents=True)
    (d / "task.toml").write_text("\n".join(f"{k} = {v}" for k, v in fields.items() if v is not None))


@pytest.mark.parametrize("overrides,msg", [
    ({"tier": None}, "missing"), ({"tier": "0"}, "tier"), ({"tier": "7"}, "tier"),
    ({"category": '"misc"'}, "category"), ({"id": '"other"'}, "directory"), ({"extra": "1"}, "unknown"),
])
def test_invalid_tasks_rejected(tmp_path, overrides, msg):
    _write_task(tmp_path, **overrides)
    with pytest.raises(ValueError, match=msg):
        load_tasks(tmp_path)


def test_workspace_reads_fixture_files(tmp_path):
    ws = load_workspace(ROOT / "fixtures" / "repo_S")
    assert "shopkit/money.py" in ws.files and "tests/test_money.py" in ws.files
    assert list(ws.files) == sorted(ws.files)
    assert not any("__pycache__" in p for p in ws.files)
    assert "def format_money" in ws.files["shopkit/money.py"]
