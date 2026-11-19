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

import json
from pathlib import Path


def write_bundle(run_dir: Path, *, patch: str | None, messages: list[dict], response: str,
                 reasoning: str | None,
                 transcript: list[dict] | None, tests_output: str, result: dict) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    for stale in run_dir.iterdir():          # overwrite: nothing from an earlier attempt survives
        stale.unlink()
    if patch:
        (run_dir / "patch.diff").write_text(patch)
    (run_dir / "prompt.json").write_text(json.dumps(messages, indent=2, ensure_ascii=False))
    (run_dir / "response.txt").write_text(response)
    if reasoning:
        (run_dir / "reasoning.txt").write_text(reasoning)
    if transcript is not None:
        (run_dir / "transcript.jsonl").write_text("".join(json.dumps(t) + "\n" for t in transcript))
    (run_dir / "tests.txt").write_text(tests_output)
    (run_dir / "run.json").write_text(json.dumps(result, indent=2))
