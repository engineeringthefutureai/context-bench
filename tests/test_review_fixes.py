"""Regression tests for the 2026-11-21 code review findings."""
import json
import threading
from pathlib import Path

import numpy as np

from context_bench.apply import parse_files_lenient
from context_bench.levels import LEVELS
from context_bench.levels.l5_function_rag import parse_queries
from context_bench.runner import _had_tool_calls, _helper_body_visible, _helper_visible, _recall
from context_bench.tasks import load_tasks
from context_bench.types import ContextBlock
from context_bench.workspace import load_workspace

ROOT = Path(__file__).resolve().parents[1]
TASKS = load_tasks(ROOT / "tasks")
M = load_workspace(ROOT / "fixtures" / "repo_M")


def test_embedding_cache_is_atomic_and_thread_safe(tmp_path):
    from context_bench.embedders import CachedEmbedder
    class Slow:
        name = "slow"
        calls = 0
        def embed(self, texts):
            Slow.calls += len(texts)
            return np.ones((len(texts), 4), dtype=np.float32) / 2
    cached = CachedEmbedder(Slow(), tmp_path)
    texts = [f"chunk {i}" for i in range(50)]
    threads = [threading.Thread(target=cached.embed, args=(texts,)) for _ in range(4)]
    [t.start() for t in threads]; [t.join() for t in threads]
    assert Slow.calls == 50                     # embedded once, not once per thread
    assert not list(cached.dir.glob("*.tmp")) and len(list(cached.dir.glob("*.npy"))) == 50


def test_rescore_keeps_tool_request_from_the_raw_message(tmp_path):
    t = tmp_path / "transcript.jsonl"
    t.write_text(json.dumps({"finish_reason": "tool_calls", "message": {"tool_calls": [{"id": "x"}]}}) + "\n")
    assert _had_tool_calls(t, "L1") and not _had_tool_calls(t, "L7")
    assert not _had_tool_calls(tmp_path / "missing.jsonl", "L1")


def test_lenient_unclosed_file_block_drops_both_fence_lines():
    assert parse_files_lenient('<file path="a.py">\n```python\nx = 1\n```', "t.py") == {"a.py": "x = 1\n"}


def test_parse_queries_skips_brackets_in_prose():
    assert parse_queries('Queries for [cart]:\n["csv export", "money format"]') == ["csv export", "money format"]
    assert parse_queries('[1, 2]\n["real query"]') == ["real query"]


def test_visibility_loose_and_strict_are_consistent_across_levels():
    trap = TASKS["trap-001"]
    l6 = LEVELS["L6"](trap, M)                       # signatures only
    assert _helper_visible(trap, l6) and not _helper_body_visible(trap, l6, M)
    grep_line = ContextBlock(text="shopkit/shipping/packaging.py:30: def format_weight(grams: int, unit: str = \"kg\") -> str:")
    assert _helper_visible(trap, grep_line) and not _helper_body_visible(trap, grep_line, M)
    l3 = LEVELS["L3"](trap, M)
    assert _helper_visible(trap, l3) and _helper_body_visible(trap, l3, M)
    decorated = ContextBlock(text="  @staticmethod def format_weight(grams)")
    assert _helper_visible(trap, decorated)


def test_recall_resolves_the_defining_file():
    trap = TASKS["trap-001"]
    by_file = ContextBlock(text="", retrieved=["shopkit/shipping/packaging.py"])
    assert _recall(trap, by_file, M) == 1.0
    method_task = trap.__class__(**{**trap.__dict__, "needed_helpers": ("shopkit.orders.models.Order.add_line",)}) \
        if any(c.qualname == "shopkit.orders.models.Order.add_line" for c in M.chunks) else None
    if method_task:
        assert _recall(method_task, ContextBlock(text="", retrieved=["shopkit/orders/models.py"]), M) == 1.0


def test_embed_stats_are_per_thread_and_count_cache_hits(tmp_path):
    from context_bench.embedders import CachedEmbedder, EmbedStats
    class Inner:
        name = "fake"
        stats = EmbedStats()
        def embed(self, texts):
            self.stats.add(embed_texts=len(texts), embed_tokens=3 * len(texts), embed_s=0.5)
            return np.ones((len(texts), 2), dtype=np.float32)
    c = CachedEmbedder(Inner(), tmp_path)
    c.stats.reset(); c.embed(["a", "b"]); c.embed(["a", "c"])
    assert c.stats.snapshot() == {"embed_texts": 3, "embed_tokens": 9, "embed_s": 1.0, "embed_cache_hits": 1}
    seen = {}
    t = threading.Thread(target=lambda: seen.update(c.stats.snapshot()))
    t.start(); t.join()
    assert seen["embed_texts"] == 0             # another thread (another run) starts from zero


def test_report_has_embedder_section():
    from context_bench.results import embedder_performance
    row = {"level": "L5", "retriever": "embed", "query_mode": "task_vs_code", "agent_variant": "none",
           "prompt_arm": "base", "repo_size": "S", "prompt_version": 4, "embedder": "e",
           "embed_texts": 10, "embed_cache_hits": 30, "embed_s": 2.0, "embed_tokens": 400}
    out = "\n".join(embedder_performance([row]))
    assert "| e | L5 embed task_vs_code | 1 | 10 | 75% | 2.00s | 200 | 5.0 |" in out


def test_format_status_empty_and_code_inside_a_pretend_tool_call():
    from context_bench.apply import format_status
    assert format_status("", {}) == "empty" and format_status("  \n", {}) == "empty"
    assert format_status("Let me check.\n<tool_call>\n<function=read_file>\n</tool_call>", {}) == "tool_request"
    wrapped = ("I have everything I need.\n<tool_call>\n<function=write>\n<parameter=content>\n"
               "from shopkit.x import y\n\ndef f():\n    return y()\n</parameter>\n</function>\n</tool_call>")
    assert format_status(wrapped, {}) == "bad_format"      # the answer is there, in the wrong wrapper


def test_agent_answer_text_falls_back_to_the_last_non_empty_reply():
    from context_bench.runner import _answer_text
    msgs = [{"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
            {"role": "assistant", "content": "I have everything I need. <tool_call>def f(): pass</tool_call>"},
            {"role": "user", "content": "Your reply couldn't be used"},
            {"role": "assistant", "content": ""}]
    assert _answer_text("", msgs).startswith("I have everything")
    assert _answer_text("final reply", msgs) == "final reply" and _answer_text("", None) == ""
