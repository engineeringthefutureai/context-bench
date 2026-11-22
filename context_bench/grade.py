"""Blind, comparison-based grading (cbench grade). Runs after the sweep; never changes labels.

For each task and repo it ran on (a patch only applies to the repo it was made against):
  1. Collect the patch of every run of that task on that repo (all levels, variants, models,
     seeds), from the current prompt version and the fixture as it is now (repo_hash), so old
     patches against an older repo never mix in. Skip runs without a patch. Identical patches are
     shown once.
  2. Write a judge directory OUTSIDE the project tree, <work_dir>/<task_id>@<repo>, wiped first so nothing
     from an earlier task or run remains. It contains only:
       repo/                    the fixture repo before any change
       task.md                  the task text as the models saw it
       candidates/<n>.diff      one patch per candidate, plus <n>.tests.txt (passed/total)
       RUBRIC.md                grading/rubric.md
     Candidates are numbered by sorted patch hash: stable, and unrelated to how they were made.
     Nothing about levels, models, prompts, context or tool use goes in.
  3. Run one headless Claude Code judge there that compares all candidates and answers with JSON:
     an overall ranking plus per-candidate scores and a note. judge_command confines it: only
     Read/Grep/Glob exist (--tools), anything outside its directory is denied (--permission-mode
     dontAsk), and no user/project settings or CLAUDE.md load (--setting-sources ""); its cwd outside
     the project also means no project memory.
  4. Map candidates back to run_ids and append one line per run to results/grades.jsonl.

report(): mean overall rank (as a percentile, so tasks with different candidate counts compare)
and mean dimension scores per configuration. rank_pct = share of the other candidates ranked
strictly above this one: 0.0 = best (or tied for best), 1.0 = worst.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

DIMENSIONS = ("correctness", "reuse", "conventions", "scope", "readability")
JUDGE_PROMPT = ("Follow RUBRIC.md in the current directory: read task.md, look at repo/ as needed, compare "
                "every candidate in candidates/, and reply with the JSON it asks for, nothing else.")


@dataclass
class Candidate:
    number: int
    patch: str
    tests_passed: int
    tests_total: int
    run_ids: list[str] = field(default_factory=list)   # every run that produced this exact patch


def collect_candidates(rows: list[dict], runs_dir: Path) -> list[Candidate]:
    """One candidate per distinct patch among `rows` (already filtered to one task and repo),
    numbered 1.. by sorted patch hash."""
    by_hash: dict[str, Candidate] = {}
    for r in rows:
        patch_file = runs_dir / r["run_id"] / "patch.diff"
        if not patch_file.is_file():
            continue
        patch = patch_file.read_text()
        h = hashlib.sha256(patch.encode()).hexdigest()
        c = by_hash.setdefault(h, Candidate(0, patch, r["tests_passed"], r["tests_total"]))
        c.run_ids.append(r["run_id"])
    ordered = [by_hash[h] for h in sorted(by_hash)]
    for i, c in enumerate(ordered, 1):
        c.number = i
    return ordered


def write_judge_dir(task_prompt: str, repo: Path, candidates: list[Candidate], rubric: Path,
                    dst: Path) -> Path:
    """Remove dst if it exists, then write the layout above."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(repo, dst / "repo", ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (dst / "task.md").write_text(task_prompt.strip() + "\n")
    shutil.copyfile(rubric, dst / "RUBRIC.md")
    cand = dst / "candidates"
    cand.mkdir()
    for c in candidates:
        (cand / f"{c.number}.diff").write_text(c.patch)
        (cand / f"{c.number}.tests.txt").write_text(f"{c.tests_passed}/{c.tests_total} hidden tests passed\n")
    return dst


def parse_verdict(text: str, numbers: list[int]) -> dict:
    """The judge's JSON (a ```json fence is tolerated). Raises ValueError unless every candidate is
    ranked exactly once and scored 1-5 on every dimension."""
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.S)
    data = json.loads(m.group(1) if m else text.strip())
    ranked = [int(n) for group in data["ranking"] for n in group]
    if sorted(ranked) != sorted(numbers):
        raise ValueError(f"ranking covers {sorted(ranked)}, expected {sorted(numbers)}")
    for n in numbers:
        scores = data["candidates"][str(n)]
        for d in DIMENSIONS:
            if not (isinstance(scores.get(d), int) and 1 <= scores[d] <= 5):
                raise ValueError(f"candidate {n}: bad {d} score {scores.get(d)!r}")
    return data


def run_judge(judge_dir: Path, judge_command: list[str], numbers: list[int], timeout: int = 1800) -> dict:
    """Run the judge in judge_dir. stdout is the CLI's JSON envelope: take its `result` (Claude Code)
    or `response` (agy) string,
    strip a surrounding ```json fence if present, parse it, and check every candidate is ranked and
    scored exactly once. Retry once on invalid output; then raise."""
    error = None
    for _ in range(2):
        proc = subprocess.run(judge_command + [JUDGE_PROMPT], cwd=judge_dir, capture_output=True, text=True,
                              timeout=timeout)
        try:
            envelope = json.loads(proc.stdout)
            text = (envelope.get("result", envelope.get("response")) if isinstance(envelope, dict)
                    else proc.stdout)                   # Claude Code: "result"; agy: "response"
            return parse_verdict(text, numbers)
        except (ValueError, KeyError, TypeError) as e:
            error = f"{type(e).__name__}: {e} (exit {proc.returncode}; stderr: {proc.stderr[-300:]})"
    raise RuntimeError(f"judge gave no valid verdict twice: {error}")


def _rank_pct(ranking: list[list[int]]) -> dict[int, float]:
    total = sum(len(g) for g in ranking)
    out, above = {}, 0
    for group in ranking:
        for n in group:
            out[int(n)] = round(above / (total - 1), 4) if total > 1 else 0.0
        above += len(group)
    return out


def grade_task(task, size: str, rows: list[dict], *, repo: Path, runs_dir: Path, grades_path: Path,
               grading: dict, rubric: Path, rubric_version: str, dry_run: bool = False) -> int:
    """Steps 1-4 for one task on one repo; returns the number of runs graded (0 on dry run).
    `rows` are that task's results on that repo, already filtered (current prompt and fixture)."""
    candidates = collect_candidates(rows, runs_dir)
    if not candidates:
        return 0
    dst = Path(grading["work_dir"]) / f"{task.id}@{size}"
    write_judge_dir(task.prompt, repo, candidates, rubric, dst)
    if dry_run:
        return 0
    numbers = [c.number for c in candidates]
    verdict = (run_judge(dst, grading["judge_command"], numbers) if len(candidates) > 1 else
               {"ranking": [[1]], "candidates": {"1": {d: None for d in DIMENSIONS} | {"note": "only candidate"}}})
    judge = grading.get("judge") or Path(grading["judge_command"][0]).name
    pct = _rank_pct(verdict["ranking"])
    rank_of = {int(n): i + 1 for i, g in enumerate(verdict["ranking"]) for n in g}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = []
    for c in candidates:
        scores = verdict["candidates"][str(c.number)]
        for run_id in c.run_ids:
            lines.append({"run_id": run_id, "task_id": task.id, "repo_size": size, "candidate": c.number,
                          "n_candidates": len(candidates), "rank": rank_of[c.number], "rank_pct": pct[c.number],
                          **{d: scores.get(d) for d in DIMENSIONS}, "note": scores.get("note", ""),
                          "rubric_version": rubric_version, "judge": judge, "graded_at": now})
    grades_path.parent.mkdir(parents=True, exist_ok=True)
    with grades_path.open("a") as f:
        f.writelines(json.dumps(line) + "\n" for line in lines)
    return len(lines)


def load_grades(grades_path: Path) -> dict[str, dict]:
    """Latest grade per run_id."""
    if not grades_path.is_file():
        return {}
    out = {}
    for line in grades_path.read_text().splitlines():
        if line.strip():
            g = json.loads(line)
            out[g["run_id"]] = g
    return out


def report(rows: list[dict], grades_path: Path) -> list[str]:
    """Markdown lines: which judge(s) graded these runs (grades from before 2026-11-22 have no `judge`
    field: claude), then per configuration, graded runs, mean rank percentile and mean scores."""
    from .results import _table, config_label
    grades = load_grades(grades_path)
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if (g := grades.get(r["run_id"])) and g["n_candidates"] > 1:
            groups.setdefault(config_label(r), []).append(g)
    judges: dict[str, int] = {}
    for gs in groups.values():
        for g in gs:
            judges[g.get("judge", "claude")] = judges.get(g.get("judge", "claude"), 0) + 1
    table = []
    for label, gs in sorted(groups.items(), key=lambda kv: sum(g["rank_pct"] for g in kv[1]) / len(kv[1])):
        mean = lambda key: sum(g[key] for g in gs if g[key] is not None) / max(1, sum(g[key] is not None for g in gs))
        table.append([label, len(gs), f"{mean('rank_pct'):.2f}"] + [f"{mean(d):.1f}" for d in DIMENSIONS])
    if not table:
        return []
    note = ["Judge: " + ", ".join(f"{j} ({n} runs)" for j, n in sorted(judges.items()))
            + (". Ranks from different judges are not comparable; regrade to one judge." if len(judges) > 1 else "."), ""]
    return note + _table(["Config", "graded runs", "rank pct (0 = best)", *DIMENSIONS], table)
