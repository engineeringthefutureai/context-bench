"""Orchestration. One run:

  1. copy the repo into a fresh sandbox
  2. build the context with LEVELS[level](task, workspace, **variant options)
  3. render the fixed prompt; count tokens; if it doesn't fit -> label no_fit and stop
  4. call the model (L8: run the tool loop)
  5. parse <file> blocks, apply them, save the patch
  6. run hidden tests, run the detector
  7. return a RunResult (helper_in_context, recall, label, cost)
"""
from __future__ import annotations

from pathlib import Path

from .types import RunConfig, RunResult


class Experiment:
    def __init__(self, config_path: Path):
        raise NotImplementedError

    def run_one(self, rc: RunConfig) -> RunResult:
        raise NotImplementedError

    def configs(self):
        """Expand [[config]] entries x models x repo sizes x seeds x tasks into RunConfigs."""
        raise NotImplementedError

    def sweep(self) -> None:
        """Run every config not already in results.jsonl; failures aren't written, so they retry."""
        raise NotImplementedError

    def build_summaries(self, repo_size: str, model_key: str) -> int:
        """Cache one-line summaries per function (for L5 task_vs_summary)."""
        raise NotImplementedError

    def validate_retrieval(self, retrievers: list[str], repo_size: str = "S") -> str:
        """recall@1/3/8 of planted helpers per retriever, function and file level."""
        raise NotImplementedError
