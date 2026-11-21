"""L8: repo tools and the tool loop (fake client, no server)."""
import json
from pathlib import Path

import pytest

from context_bench.levels import LEVELS
from context_bench.levels.l8_agentic import BUDGET_SPENT, MAX_TOOL_CALLS, RepoTools, run_agent, tool_schemas
from context_bench.llm import ChatResult
from context_bench.tasks import load_tasks
from context_bench.workspace import load_workspace

ROOT = Path(__file__).resolve().parents[1]
M = ROOT / "fixtures" / "repo_M"
TASKS = load_tasks(ROOT / "tasks")


def test_context_is_target_plus_tools_note_and_schemas_match():
    ctx = LEVELS["L8"](TASKS["reuse-001"], load_workspace(ROOT / "fixtures/repo_S"))
    assert ctx.text.startswith("File: shopkit/orders/export.py") and "grep, list_files, read_file, find_references" in ctx.text
    assert [t["function"]["name"] for t in tool_schemas("grep")] == ["grep", "list_files", "read_file", "find_references"]
    assert tool_schemas("hybrid")[-1]["function"]["name"] == "semantic_search"
    for word in ("dead", "legacy", "unused", "reuse", "helper"):
        assert word not in ctx.text.lower().split("```")[-1]


def test_tools_on_repo_m():
    t = RepoTools(M)
    listing = t.list_files("shopkit/common")
    assert "shopkit/common/TaxHelper.py  (25 lines, imported by 1 modules, 1 of them tests)" in listing
    assert "imported by 12 modules" in t.list_files("shopkit/utils")          # dates.py: live
    refs = t.find_references("shopkit.shipping.packaging.format_weight")
    assert "definitions (1):\n  shopkit/shipping/packaging.py:30:" in refs and "shopkit/orders/fulfillment.py:40:" in refs
    assert "shopkit/utils/text.py:11: def slug_for_title" in t.grep(r"def slug", "shopkit/**")
    assert t.read_file("shopkit/money.py").startswith("File: shopkit/money.py\n")
    assert t.read_file("nope.py").startswith("error:")
    with pytest.raises(ValueError):
        t.read_file("../../etc/passwd")


class FakeClient:
    """Replies from a script: each item is a list of (tool, args) calls, or a final string."""
    def __init__(self, script):
        self.script, self.seen = list(script), []

    def count_tokens(self, text):
        return len(text) // 4

    def count_prompt_tokens(self, msgs):
        return sum(len(m.get("content") or "") for m in msgs) // 4

    def chat(self, msgs, *, temperature, seed, max_tokens, tools=None, thinking=True):
        self.seen.append((list(msgs), tools))
        step = self.script.pop(0)
        if isinstance(step, str):
            return ChatResult(content=step, tokens_in=10, tokens_out=5, finish_reason="stop")
        calls = [{"id": f"c{i}", "type": "function",
                  "function": {"name": n, "arguments": a if isinstance(a, str) else json.dumps(a)}}
                 for i, (n, a) in enumerate(step)]
        return ChatResult(content="", tool_calls=calls, tokens_in=10, tokens_out=5, finish_reason="tool_calls")


def _run(script, window=100_000):
    client = FakeClient(script)
    res = run_agent(client, [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                    RepoTools(M), agent_variant="grep", temperature=None, seed=1, max_tokens=100,
                    context_window=window)
    return client, res


def test_loop_runs_tools_then_returns_final_answer():
    client, res = _run([[("read_file", {"path": "shopkit/money.py"}), ("bogus", {})], "<file path=\"a.py\">x</file>"])
    assert res.content.startswith("<file") and res.tool_calls == 2 and res.tool_errors == 1
    assert res.tokens_in == 20 and res.tokens_out == 10 and not res.no_fit
    tool_msgs = [m for m in client.seen[1][0] if m["role"] == "tool"]
    assert tool_msgs[0]["content"].startswith("File: shopkit/money.py") and tool_msgs[1]["content"].startswith("error:")


def test_budget_is_enforced_then_tools_are_withdrawn():
    many = [("grep", {"pattern": "def"})] * (MAX_TOOL_CALLS + 2)
    client, res = _run([many, "final"])
    assert res.tool_calls == MAX_TOOL_CALLS and res.tool_errors == 2 and res.content == "final"
    last_msgs, last_tools = client.seen[-1]
    assert last_tools is None and last_msgs[-1] == {"role": "user", "content": BUDGET_SPENT}


def test_no_fit_stops_before_the_server_truncates():
    _, res = _run([[("read_file", {"path": "shopkit/money.py"})], "final"], window=400)
    assert res.no_fit and res.content == ""


def test_semantic_search_uses_the_supplied_retriever():
    from context_bench.retrieval.bm25 import BM25Retriever
    t = RepoTools(M, make_retriever=BM25Retriever)
    out = t.semantic_search("format weight grams", k=2)
    assert out.splitlines()[0].startswith("shopkit/shipping/packaging.py:") and "format_weight" in out.splitlines()[0]
    with pytest.raises(RuntimeError):
        RepoTools(M).semantic_search("x")


def test_cacheable_tokens_count_the_repeated_prefix():
    _, res = _run([[("read_file", {"path": "shopkit/money.py"})], [("grep", {"pattern": "def"})], "final"])
    assert res.tokens_in == 30 and res.tokens_cacheable == 20      # turns 2 and 3 repeat the previous prompt
    assert res.tokens_cached is None                               # the fake server doesn't report hits


def test_cached_tokens_parsed_from_either_server():
    from context_bench.llm import _cached
    assert _cached({"prompt_tokens_details": {"cached_tokens": 512}}, {}) == 512
    assert _cached({}, {"cache_n": 300}) == 300
    assert _cached({"prompt_tokens_details": None}, {}) is None
