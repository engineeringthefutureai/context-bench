"""Skeleton check: every module imports and every level is registered."""
import importlib

MODULES = [
    "context_bench.types", "context_bench.workspace", "context_bench.prompt", "context_bench.llm", "context_bench.chunking",
    "context_bench.tokenize", "context_bench.embedders", "context_bench.apply", "context_bench.tasks", "context_bench.results",
    "context_bench.runner", "context_bench.cli", "context_bench.retrieval.base", "context_bench.retrieval.bm25",
    "context_bench.retrieval.dense", "context_bench.evaluate.hidden_tests",
    "context_bench.levels", "context_bench.bundle",
    "context_bench.grade",
]


def test_modules_import():
    for m in MODULES:
        importlib.import_module(m)


def test_all_levels_registered():
    from context_bench.levels import LEVELS
    assert list(LEVELS) == [f"L{i}" for i in range(9)] + ["L-AG"]   # L8, L-AG: experimental
