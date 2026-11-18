"""Duplication detector.

Label precedence: no_fit > tool_error > hallucinated > broken > reuse classification.
Reuse classification (reuse tasks only):
  1. duplicated   a new top-level function agrees with a needed helper on >= 95% of fuzzed inputs
  2. reused       the patch references every needed helper and doesn't shadow it
  3. inlined_dup  tests pass, helper unused, no equivalent function (needs_review = True)
Non-reuse tasks: ok if tests pass, else broken.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

EQUIV_THRESHOLD = 0.95


@dataclass(frozen=True)
class Helper:
    qualname: str
    fuzz: str                  # strategy name in fuzz.py
    naming: str = "matching"   # matching | opaque: does task wording share tokens with the name?


@dataclass
class Verdict:
    label: str
    equiv_score: float | None = None
    needs_review: bool = False
    detail: str = ""


def load_registry(path: Path) -> dict[str, Helper]:
    raise NotImplementedError


def hallucinated_imports(sandbox: Path, changed: dict[str, str], package_roots: set[str]) -> list[str]:
    """Project symbols imported by the patch that don't exist after applying it."""
    raise NotImplementedError


def new_top_level_functions(before: dict[str, str | None], after: dict[str, str]) -> list[tuple[str, str]]:
    """(module, function_name) for top-level functions the patch added."""
    raise NotImplementedError


def equivalence(sandbox: Path, helper: Helper, cand_mod: str, cand_name: str, n: int = 500) -> float:
    """Share of fuzzed inputs where candidate and helper agree (same value, or both raise).
    Runs in a subprocess inside the sandbox."""
    raise NotImplementedError


def classify(*, category: str, sandbox: Path, before: dict[str, str | None], after: dict[str, str],
             needed: list[Helper], tests_passed: int, tests_total: int,
             package_roots: set[str]) -> Verdict:
    raise NotImplementedError
