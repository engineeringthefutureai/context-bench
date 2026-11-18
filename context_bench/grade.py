"""Blind, comparison-based grading (cbench grade). Runs after the sweep; never changes labels.

For each task:
  1. Collect the patch of every run of that task (all levels, variants, models, seeds). Skip runs
     without a patch. Identical patches are shown once.
  2. Write a judge directory OUTSIDE the project tree, <work_dir>/<task_id>, wiped first so nothing
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
and mean dimension scores per configuration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DIMENSIONS = ("correctness", "reuse", "conventions", "scope", "readability")


@dataclass
class Candidate:
    number: int
    patch: str
    tests_passed: int
    tests_total: int
    run_ids: list[str] = field(default_factory=list)   # every run that produced this exact patch


def collect_candidates(task_id: str, results_path: Path, runs_dir: Path) -> list[Candidate]:
    raise NotImplementedError


def write_judge_dir(task_prompt: str, repo: Path, candidates: list[Candidate], rubric: Path,
                    dst: Path) -> Path:
    """Remove dst if it exists, then write the layout above."""
    raise NotImplementedError


def run_judge(judge_dir: Path, judge_command: list[str], timeout: int = 1800) -> dict:
    """Run the judge in judge_dir. stdout is the CLI's JSON envelope: take its `result` string,
    strip a surrounding ```json fence if present, parse it, and check every candidate is ranked and
    scored exactly once. Retry once on invalid output; then raise."""
    raise NotImplementedError


def grade_task(task_id: str, *, config: dict, dry_run: bool = False) -> int:
    """Steps 1-4 for one task; returns the number of runs graded (0 on dry run)."""
    raise NotImplementedError


def report(results_path: Path, grades_path: Path) -> str:
    raise NotImplementedError
