"""How much of a patch the hidden tests actually run, and whether the patch adds tests of its own.

patch_coverage: the patch's changed statements (non-test .py files) that ran while the task's hidden
tests (correctness + reuse) ran. A PASS with low coverage means much of the new code is unverified.
A statement counts once (the innermost statement holding a changed line); def/class lines and
docstrings are left out (a def line runs at import, so it says nothing). Blank and comment lines
are ignored.

tests_added: new test functions (def test_*) in test files the patch creates or changes.
Descriptive only: the prompt never asks for tests.
"""
from __future__ import annotations

import ast
import difflib
import json
import os
import tempfile
from pathlib import Path

from .hidden_tests import _pytest

_STRUCTURE = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def is_test_path(rel: str) -> bool:
    return rel.startswith("tests/") or "/tests/" in rel or Path(rel).name.startswith("test_")


def changed_statements(before: dict[str, str | None], files: dict[str, str]) -> dict[str, set[int]]:
    """rel path -> line numbers (in the new file) of the statements the patch changed or added."""
    out: dict[str, set[int]] = {}
    for rel, new in files.items():
        if not rel.endswith(".py") or is_test_path(rel):
            continue
        old_lines, new_lines = (before.get(rel) or "").splitlines(), new.splitlines()
        changed = set()
        for tag, _, _, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
            if tag in ("replace", "insert"):
                changed.update(i for i in range(j1 + 1, j2 + 1)
                               if new_lines[i - 1].strip() and not new_lines[i - 1].strip().startswith("#"))
        try:
            tree = ast.parse(new)
        except SyntaxError:
            continue
        stmts = [n for n in ast.walk(tree) if isinstance(n, ast.stmt) and not isinstance(n, _STRUCTURE)
                 and not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                          and isinstance(n.value.value, str))]
        keys = set()
        for line in changed:
            inner = [n for n in stmts if n.lineno <= line <= (n.end_lineno or n.lineno)]
            if inner:
                keys.add(max(inner, key=lambda n: (n.lineno, -(n.end_lineno or n.lineno))).lineno)
        if keys:
            out[rel] = keys
    return out


def run_hidden_traced(sandbox: Path, tests_dir: Path, shared_dir: Path, timeout: int = 180) -> dict[str, set[int]] | None:
    """Run every hidden test (correctness and reuse) once, recording executed lines under the sandbox:
    realpath -> line numbers. None if the run produced no trace (e.g. the tracer plugin didn't load)."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "trace.json"
        # the tracer plugin is context_bench.evaluate._linetrace: make it importable without an install
        # (the sandbox stays first on the path, so nothing here shadows the fixture's own modules)
        pkg_root = str(Path(__file__).resolve().parents[2])
        env = {"CBENCH_TRACE_ROOT": str(sandbox), "CBENCH_TRACE_OUT": str(out),
               "PYTHONPATH": os.pathsep.join([str(sandbox), str(shared_dir), pkg_root])}
        _pytest(sandbox, shared_dir, timeout, ["-p", "context_bench.evaluate._linetrace", str(tests_dir)],
                extra_env=env)
        if not out.exists():
            return None
        return {k: set(v) for k, v in json.loads(out.read_text()).items()}


def patch_coverage(sandbox: Path, tests_dir: Path, shared_dir: Path, before: dict[str, str | None],
                   files: dict[str, str]) -> dict:
    """patch_stmts, patch_stmts_covered, patch_coverage (None when the patch changes no statements
    outside tests) for a sandbox that already has `files` applied."""
    changed = changed_statements(before, files)
    total = sum(len(v) for v in changed.values())
    if not total:
        return {"patch_stmts": 0, "patch_stmts_covered": 0, "patch_coverage": None}
    hits = run_hidden_traced(sandbox, tests_dir, shared_dir)
    if hits is None:                 # no trace: unknown, never a silent 0% coverage
        return {"patch_stmts": total, "patch_stmts_covered": None, "patch_coverage": None}
    root = os.path.realpath(sandbox)
    covered = sum(len(lines & hits.get(os.path.join(root, rel), set())) for rel, lines in changed.items())
    return {"patch_stmts": total, "patch_stmts_covered": covered, "patch_coverage": round(covered / total, 3)}


def tests_added(before: dict[str, str | None], files: dict[str, str]) -> dict:
    """tests_added (new def test_* names in changed test files), test_files_changed."""
    def names(src: str | None) -> set[str]:
        try:
            return {n.name for n in ast.walk(ast.parse(src or ""))
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test")}
        except SyntaxError:
            return set()
    test_files = [rel for rel in files if rel.endswith(".py") and is_test_path(rel)]
    return {"tests_added": sum(len(names(files[r]) - names(before.get(r))) for r in test_files),
            "test_files_changed": len(test_files)}
