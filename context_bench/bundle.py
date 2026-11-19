"""Per-run artifacts in results/runs/<run_id>/, for auditing, debugging and grading:

  patch.diff        unified diff of the change; absent if the response had no <file> blocks
  prompt.json       the exact chat messages sent
  response.txt      raw model output (final message for L8)
  reasoning.txt     the model's thinking (reasoning_content), if the server returns it
  transcript.jsonl  L8 only: one line per tool call {name, arguments, output, error}
  tests.txt         raw hidden-test output
  run.json          the RunResult record

Grading reads only patch.diff and run.json (for the test counts).
"""
from __future__ import annotations

from pathlib import Path


def write_bundle(run_dir: Path, *, patch: str | None, messages: list[dict], response: str,
                 reasoning: str | None,
                 transcript: list[dict] | None, tests_output: str, result: dict) -> None:
    raise NotImplementedError
