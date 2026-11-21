"""cbench grade with a fake judge: blind dir layout, candidate de-duplication, verdict checks, grades."""
import json
import sys
from pathlib import Path

import pytest

from context_bench.grade import (DIMENSIONS, _rank_pct, collect_candidates, grade_task, load_grades,
                                 parse_verdict, report, write_judge_dir)
from context_bench.tasks import load_tasks

ROOT = Path(__file__).resolve().parents[1]
TASK = load_tasks(ROOT / "tasks")["reuse-002"]
PATCH_A = "--- a/x.py\n+++ b/x.py\n@@ -0,0 +1 @@\n+a = 1\n"
PATCH_B = "--- a/x.py\n+++ b/x.py\n@@ -0,0 +1 @@\n+b = 2\n"


def _rows(tmp_path):
    runs = tmp_path / "runs"
    rows = []
    for run_id, level, patch in (("r1", "L1", PATCH_A), ("r2", "L3", PATCH_B), ("r3", "L7", PATCH_A), ("r4", "L0", None)):
        (runs / run_id).mkdir(parents=True)
        if patch:
            (runs / run_id / "patch.diff").write_text(patch)
        rows.append({"run_id": run_id, "level": level, "retriever": "none", "query_mode": "none",
                     "agent_variant": "none", "prompt_arm": "base", "repo_size": "S", "prompt_version": 3,
                     "tests_passed": 1, "tests_total": 2})
    return rows, runs


def _verdict(ranking):
    return {"ranking": ranking, "candidates": {str(n): {**{d: 3 for d in DIMENSIONS}, "note": "ok"}
                                               for g in ranking for n in g}}


def _judge(tmp_path, reply):
    script = tmp_path / "judge.py"
    script.write_text(f"import json, sys\nprint(json.dumps({{'result': {reply!r}}}))\n")
    return [sys.executable, str(script)]


def test_candidates_dedupe_and_skip_runs_without_patch(tmp_path):
    rows, runs = _rows(tmp_path)
    cands = collect_candidates(rows, runs)
    assert len(cands) == 2 and sorted(len(c.run_ids) for c in cands) == [1, 2]
    assert [c.number for c in cands] == [1, 2]


def test_judge_dir_is_blind(tmp_path):
    rows, runs = _rows(tmp_path)
    d = write_judge_dir(TASK.prompt, ROOT / "fixtures" / "repo_S", collect_candidates(rows, runs),
                        ROOT / "grading" / "rubric.md", tmp_path / "judge" / "reuse-002@S")
    assert {p.name for p in d.iterdir()} == {"repo", "task.md", "RUBRIC.md", "candidates"}
    text = " ".join(p.read_text() for p in (d / "candidates").iterdir())
    assert not any(s in text for s in ("r1", "r2", "L1", "L3", "L7", "level", "model"))


def test_verdict_must_cover_every_candidate():
    assert parse_verdict("```json\n" + json.dumps(_verdict([[2], [1]])) + "\n```", [1, 2])["ranking"] == [[2], [1]]
    with pytest.raises(ValueError):
        parse_verdict(json.dumps(_verdict([[1]])), [1, 2])
    bad = _verdict([[1], [2]]); bad["candidates"]["2"]["reuse"] = 9
    with pytest.raises(ValueError):
        parse_verdict(json.dumps(bad), [1, 2])


def test_rank_pct_with_ties():
    assert _rank_pct([[2], [1, 3], [4]]) == {2: 0.0, 1: 0.3333, 3: 0.3333, 4: 1.0}


def test_grade_task_writes_one_grade_per_run(tmp_path):
    rows, runs = _rows(tmp_path)
    grades = tmp_path / "grades.jsonl"
    grading = {"work_dir": str(tmp_path / "judge"),
               "judge_command": _judge(tmp_path, "```json\n" + json.dumps(_verdict([[2], [1]])) + "\n```")}
    n = grade_task(TASK, "S", rows, repo=ROOT / "fixtures" / "repo_S", runs_dir=runs, grades_path=grades,
                   grading=grading, rubric=ROOT / "grading" / "rubric.md", rubric_version="1")
    g = load_grades(grades)
    assert n == 3 and set(g) == {"r1", "r2", "r3"}
    assert g["r2"]["rank_pct"] == 0.0 and g["r1"]["rank_pct"] == g["r3"]["rank_pct"] == 1.0
    lines = report(rows, grades)
    assert any("L3" in line and "0.00" in line for line in lines)


def test_invalid_judge_output_raises_after_a_retry(tmp_path):
    rows, runs = _rows(tmp_path)
    grading = {"work_dir": str(tmp_path / "judge"), "judge_command": _judge(tmp_path, "no json here")}
    with pytest.raises(RuntimeError, match="twice"):
        grade_task(TASK, "S", rows, repo=ROOT / "fixtures" / "repo_S", runs_dir=runs,
                   grades_path=tmp_path / "g.jsonl", grading=grading, rubric=ROOT / "grading" / "rubric.md",
                   rubric_version="1")
