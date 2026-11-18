"""Orchestration. One run:

  1. copy the repo into a fresh sandbox
  2. build the context with LEVELS[level](task, workspace, **variant options)
  3. render the fixed prompt; count tokens; if tokens + max_output_tokens > context_window
     -> ctx_fits=false, label no_fit, stop
  4. call the model (L8: run the tool loop, which applies the same fit check before every turn);
     log usage to stderr as it goes (tokens in/out,
     peak context vs window, latency, tool calls) so it's visible during testing
  5. parse <file> blocks, apply them, save the patch
  6. run hidden tests, run the detector
  7. write the run bundle (context_bench/bundle.py) and return a RunResult
     (helper_in_context, recall, label, cost)

Grading is a separate, later stage: `cbench grade` (context_bench/grade.py).
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
        """Expand [[config]] entries x models x repo sizes x seeds x tasks into RunConfigs.
        A config's optional `categories` and `tiers` keep only matching tasks (e.g. L0: tier 1)."""
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
