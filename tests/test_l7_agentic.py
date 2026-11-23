"""L7: repo tools and the tool loop (fake client, no server)."""
import json
from pathlib import Path

import pytest

from context_bench.levels import LEVELS
from context_bench.levels.l7_agentic import (BUDGET_SPENT, MAX_TOOL_CALLS, UNPARSED_CALL, RepoTools, run_agent,
                                          tool_schemas)
from context_bench.llm import ChatResult
from context_bench.tasks import load_tasks
from context_bench.workspace import load_workspace

ROOT = Path(__file__).resolve().parents[1]
M = ROOT / "fixtures" / "repo_M"
TASKS = load_tasks(ROOT / "tasks")


def test_context_is_target_plus_tools_note_and_schemas_match():
    ctx = LEVELS["L7"](TASKS["reuse-001"], load_workspace(ROOT / "fixtures/repo_S"))
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


def test_budget_turn_is_not_cacheable_and_turns_rebuild():
    from context_bench.levels.l7_agentic import cacheable_prefix, turn_prompts
    assert cacheable_prefix(100, True, 150, True) == 100
    assert cacheable_prefix(100, True, 150, False) == 0 and cacheable_prefix(0, None, 50, True) == 0
    many = [("grep", {"pattern": "def"})] * MAX_TOOL_CALLS
    _, res = _run([many, "final"])
    assert res.tokens_cacheable == 0                 # turn 2 dropped the tools: new prefix
    base = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    tr = [{"role": "assistant", "content": "", "tool_calls": [{}]}, {"role": "tool", "content": "x"},
          {"role": "user", "content": BUDGET_SPENT}, {"role": "assistant", "content": "final"}]
    turns = turn_prompts(base, tr)
    assert [len(m) for m, _ in turns] == [2, 5] and [o for _, o in turns] == [True, False]


def _run_fc(script, cost=0.5):
    client = FakeClient(script)
    res = run_agent(client, [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                    RepoTools(M), agent_variant="grep", temperature=None, seed=1, max_tokens=100,
                    context_window=100_000, failed_call_cost=cost)
    return client, res


def test_bad_arguments_get_the_valid_ones_and_cost_half():
    client, res = _run_fc([[("grep", {"pattern": "def", "path": "x"}), ("bogus", {})], "<file path=\"a.py\">x</file>"])
    errs = [m["content"] for m in client.seen[1][0] if m["role"] == "tool"]
    assert "grep takes: pattern (string, required), path_glob (string, optional)." in errs[0]
    assert "Available tools: grep, list_files, read_file, find_references." in errs[1]
    assert res.tool_calls == 2 and res.tool_errors == 2 and res.budget_used == 1.0


def test_text_tool_call_gets_feedback_and_the_loop_goes_on():
    text_call = "<tool_call>\n<function=grep>\n<parameter=pattern>\ndef\n</parameter>\n</function>\n</tool_call>"
    client, res = _run_fc([text_call, [("read_file", {"path": "shopkit/money.py"})], "<file path=\"a.py\">x</file>"])
    assert res.content.startswith("<file") and len(client.seen) == 3
    assert client.seen[1][0][-1] == {"role": "user", "content": UNPARSED_CALL}
    assert res.tool_calls == 2 and res.tool_errors == 1 and res.budget_used == 1.5


def test_original_harness_ends_on_a_text_tool_call():
    client, res = _run(["<tool_call><function=grep></function></tool_call>"])
    assert len(client.seen) == 1 and res.tool_calls == 0


def test_failed_calls_alone_cannot_loop_forever():
    client, res = _run_fc(["<tool_call>x</tool_call>"] * (2 * MAX_TOOL_CALLS) + ["final"])
    assert res.content == "final" and res.budget_used == MAX_TOOL_CALLS
    assert client.seen[-1][1] is None            # last turn: tools withdrawn after BUDGET_SPENT


# ---- rich / selective -------------------------------------------------------------------------------
def _run_sel(script, variant="selective"):
    from context_bench.retrieval.bm25 import BM25Retriever
    client = FakeClient(script)
    res = run_agent(client, [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                    RepoTools(M, make_retriever=BM25Retriever, rich=variant in ("rich", "selective")),
                    agent_variant=variant, temperature=None, seed=1, max_tokens=100,
                    context_window=100_000, failed_call_cost=0.5)
    return client, res


def test_grep_and_hybrid_tools_and_schemas_unchanged():
    """The new variants must not change what grep/hybrid send or return (recorded runs stay comparable)."""
    from context_bench.retrieval.bm25 import BM25Retriever
    names = [t["function"]["description"] for t in tool_schemas("hybrid")]
    assert names[2] == "Read a whole file." and "with their source" not in names[4]
    t = RepoTools(M, make_retriever=BM25Retriever)
    assert t.call("read_file", {"path": "shopkit/money.py"}).startswith("File: shopkit/money.py\n")
    assert "shopkit/shipping/packaging.py:30: def format_weight" in t.call("find_references", {"symbol": "format_weight"})
    hit = t.call("semantic_search", {"query": "format weight grams", "k": 1})
    assert hit.count("\n") <= 2                       # header, signature, docstring line: no bodies
    with pytest.raises(TypeError):
        t.call("read_file", {"path": "shopkit/money.py", "start": 1})
    ctx = LEVELS["L7"](TASKS["reuse-001"], load_workspace(M), agent_variant="hybrid")
    assert "[#" not in ctx.text and "drop" not in ctx.text


def test_rich_read_file_ranges_and_cap(tmp_path):
    t = RepoTools(M, rich=True)
    out = t.call("read_file", {"path": "shopkit/shipping/packaging.py", "start": 30, "end": 32})
    src = (M / "shopkit/shipping/packaging.py").read_text().splitlines()
    body = out.split("\n\n")[-1].splitlines()            # after the once-per-file imports header
    assert out.startswith("Imports of shopkit/shipping/packaging.py:\n")
    assert body[0].startswith("File: shopkit/shipping/packaging.py (lines 30-32 of ") and body[1:] == src[29:32]
    whole = t.call("read_file", {"path": "shopkit/money.py"})
    assert (M / "shopkit/money.py").read_text().strip() in whole   # verbatim: helper_body check still works
    (tmp_path / "big.py").write_text("\n".join(f"x{i} = {i}" for i in range(500)))
    big = RepoTools(tmp_path, rich=True).call("read_file", {"path": "big.py"})
    assert "(lines 1-200 of 500; capped at 200 lines, continue from line 201)" in big and big.count("\n") == 200
    with pytest.raises(ValueError):
        t.call("read_file", {"path": "shopkit/money.py", "start": 5, "end": 2})


def test_rich_search_returns_bodies_and_refs_name_the_enclosing_function():
    from context_bench.retrieval.bm25 import BM25Retriever
    t = RepoTools(M, make_retriever=BM25Retriever, rich=True)
    out = t.call("semantic_search", {"query": "format weight grams", "k": 9})
    hits = [b for b in out.split("\n\n") if b and not b.startswith("Imports of ")]
    assert 1 <= len(hits) <= 5 and "def format_weight" in out and "return" in out
    assert all(len(h.splitlines()) <= 1 + 1 + 40 + 1 for h in hits)
    refs = t.call("find_references", {"symbol": "format_weight"})
    assert "shopkit/shipping/packaging.py:30 in format_weight (lines 30-" in refs
    assert "shopkit/orders/fulfillment.py:40 in " in refs


def test_selective_cacheable_prefix_ends_at_the_dropped_message():
    client, res = _run_sel([[("read_file", {"path": "shopkit/money.py"})],
                            [("drop", {"ids": [1]}), ("grep", {"pattern": "def"})], "final"])
    # turn 2 repeats turn 1 (10); turn 3 shares only the prefix before the stubbed message
    shared = client.count_prompt_tokens(client.seen[2][0][:3])
    assert res.tokens_cacheable == 10 + min(shared + client.count_tokens(json.dumps(tool_schemas("selective"))), 10)


def test_selective_rereads_and_turn_cap_and_rich_has_no_drop():
    read = ("read_file", {"path": "shopkit/money.py", "start": 1, "end": 5})
    _, res = _run_sel([[read], [("drop", {"ids": [1]})], [("read_file", {"path": "shopkit/money.py", "start": 3, "end": 9})],
                       "final"])
    assert res.rereads_after_drop == 1
    _, res = _run_sel([[("drop", {"ids": [1]})], "final"], variant="rich")
    assert res.drops is None and res.tool_errors == 1                     # rich has no drop tool
    ctx = LEVELS["L7"](TASKS["reuse-001"], load_workspace(M), agent_variant="selective")
    assert "drop" in ctx.text.split("```")[-1]
    for word in ("dead", "legacy", "unused", "reuse", "helper", "decoy"):
        assert word not in ctx.text.lower().split("```")[-1]


def test_rich_grep_names_the_enclosing_entity_and_read_symbol_pulls_several():
    t = RepoTools(M, rich=True)
    g = t.call("grep", {"pattern": r"def format_weight", "path_glob": "shopkit/**"})
    assert g.startswith("shopkit/shipping/packaging.py:30 in format_weight (lines 30-")
    top = t.call("grep", {"pattern": r"^from shopkit.money", "path_glob": "shopkit/invoices/render.py"})
    assert "module level" in top
    out = t.call("read_symbol", {"symbols": ["shopkit.shipping.packaging.format_weight", "format_money", "nope_xyz"]})
    src = {c.qualname: c.source for c in load_workspace(M).chunks}
    assert src["shopkit.shipping.packaging.format_weight"].strip() in out      # verbatim, uncapped
    assert "  shopkit.money.format_money\n" in out and "(no function, method or class named nope_xyz)" in out
    many = t.call("read_symbol", {"symbols": ["__init__"]})
    assert "matches several definitions" in many
    # a method comes with its class header line
    meth = next(q for q, s in src.items() if s.lstrip().startswith("def ") and q.rsplit(".", 1)[0] in src
                and src[q.rsplit(".", 1)[0]].lstrip().startswith("class "))
    cls_header = src[meth.rsplit(".", 1)[0]].splitlines()[0]
    assert cls_header in t.call("read_symbol", {"symbols": [meth]})
    names = [s["function"]["name"] for s in tool_schemas("rich")]
    assert names == ["grep", "list_files", "read_symbol", "read_file", "find_references", "semantic_search"]
    assert "read_symbol is cheaper" in tool_schemas("rich")[3]["function"]["description"]
    assert "read_symbol" not in json.dumps(tool_schemas("hybrid"))


def test_selective_drop_live_tail_stub_and_budget():
    read = ("read_file", {"path": "shopkit/money.py"})                       # money.py has no imports
    client, res = _run_sel([[read, ("grep", {"pattern": "def format_money"})],   # #1 code, #2 result
                            [("drop", {"ids": [1]}), ("grep", {"pattern": "class"})],  # drop #1, #3
                            [("drop", {"ids": [2]})],                            # #2 is no longer in the tail
                            "final"])
    assert res.drops == 1 and res.drop_calls == 1 and res.tool_errors == 1
    assert res.budget_used == 3 + 0.5                # drop is free, the bad drop costs half
    turn3 = client.seen[2][0]
    n = len((M / "shopkit/money.py").read_text().splitlines())
    stub = [m for m in turn3 if m["role"] == "tool" and m["content"] == f"[#1 dropped: read_file shopkit/money.py:1-{n}]"]
    assert len(stub) == 1 and stub[0]["tool_call_id"] == "c0"
    assert any(m["role"] == "tool" and m["content"].startswith("[#2] grep ") for m in turn3)
    bad = client.seen[3][0][-1]["content"]
    assert "can't drop #2" in bad and "now: #3" in bad
    # the transcript keeps the original result and the drop record; turn_prompts replays the stub
    from context_bench.levels.l7_agentic import turn_prompts
    orig = [m for m in res.transcript if m.get("result_ids") == [1]][0]
    assert orig["content"].startswith(f"[#1] read_file shopkit/money.py:1-{n}\nFile: shopkit/money.py")
    rebuilt = turn_prompts([{"role": "system", "content": "s"}, {"role": "user", "content": "u"}], res.transcript)
    assert [m["content"] for m in rebuilt[2][0]] == [m["content"] for m in turn3]
    assert res.dropped[0]["id"] == 1 and not res.dropped[0]["auto"]


def test_rich_bodies_bring_their_file_imports_once():
    t = RepoTools(M, rich=True)
    rel = "shopkit/invoices/render.py"
    first = t.call("read_symbol", {"symbols": ["shopkit.invoices.render.render_invoice_text"]})
    assert first.startswith(f"Imports of {rel}:\nfrom shopkit.money import format_money")
    assert [x["kind"] for x in t.last_segments] == ["imports", "code"]
    again = t.call("read_file", {"path": rel, "start": 6, "end": 8})
    assert "Imports of" not in again                                   # no duplicates
    other = RepoTools(M, rich=True)
    assert "Imports of" not in other.call("read_file", {"path": rel})   # a whole read shows them itself
    assert other.last_segments[0]["provides_imports"]
    assert "Imports of" not in other.call("read_symbol", {"symbols": ["shopkit.invoices.render.render_invoice_text"]})


def _sel(script):
    from context_bench.retrieval.bm25 import BM25Retriever
    client = FakeClient(script)
    res = run_agent(client, [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                    RepoTools(M, make_retriever=BM25Retriever, rich=True), agent_variant="selective",
                    temperature=None, seed=1, max_tokens=100, context_window=100_000, failed_call_cost=0.5)
    return client, res


def test_selective_imports_stay_while_any_code_from_the_file_does():
    three = ("read_symbol", {"symbols": ["shopkit.utils.dates.is_business_day", "shopkit.utils.dates.add_business_days",
                                         "shopkit.utils.dates.iso_week_label"]})
    # #1 imports, #2-#4 the three functions; drop the first function: imports stay
    client, res = _sel([[three], [("drop", {"ids": [2]})], "final"])
    msg = [m for m in client.seen[2][0] if m["role"] == "tool"][0]["content"]
    assert msg.startswith("[#1] imports of shopkit/utils/dates.py\nImports of") and "[#2 dropped: read_symbol" in msg
    assert "[#3] read_symbol shopkit.utils.dates.add_business_days" in msg
    # the model can't drop imports itself
    client, res = _sel([[three], [("drop", {"ids": [1]})], "final"])
    assert "they leave with the last code from that file" in client.seen[2][0][-1]["content"] and res.drops == 0
    # dropping all three takes the imports along (same batch); the next code from the file brings them back
    one = ("read_symbol", {"symbols": ["shopkit.utils.dates.iso_week_label"]})
    client, res = _sel([[three], [("drop", {"ids": [2, 3, 4]}), one], "final"])
    msgs = [m["content"] for m in client.seen[2][0] if m["role"] == "tool"]
    assert msgs[0].startswith("[#1 dropped: imports of shopkit/utils/dates.py]") and "imports no longer needed" in msgs[1]
    assert msgs[2].startswith("[#5] imports of shopkit/utils/dates.py") and res.drops == 3
    assert [d["auto"] for d in res.dropped] == [False, False, False, True]


def test_selective_imports_follow_the_last_code_and_return_with_new_code():
    """With a live tail, a file's imports always share a message with its first code, so they can only
    lose their last code while both are in the tail: the rule never rewrites an older message."""
    a = ("read_symbol", {"symbols": ["shopkit.utils.dates.is_business_day"]})      # #1 imports, #2 code
    b = ("read_symbol", {"symbols": ["shopkit.utils.dates.iso_week_label"]})       # #3 imports again, #4 code
    c = ("read_symbol", {"symbols": ["shopkit.utils.dates.add_business_days"]})    # #5 code, imports already there
    client, res = _sel([[a], [("drop", {"ids": [2]}), b], [c], [("drop", {"ids": [5]})], "final"])
    tool = [m["content"] for m in client.seen[4][0] if m["role"] == "tool"]
    assert tool[0].startswith("[#1 dropped: imports of") and tool[2].startswith("[#3] imports of")
    assert tool[3].startswith("[#5 dropped: read_symbol") and "imports no longer needed" not in tool[4]
    assert res.drops == 2 and [d["auto"] for d in res.dropped] == [False, True, False]


def test_selective_measures_the_context_saved_by_drops():
    read = ("read_symbol", {"symbols": ["shopkit.shipping.packaging.format_weight"]})   # #1 imports, #2 code
    grep = ("grep", {"pattern": "def "})
    client, res = _sel([[read], [("drop", {"ids": [2]}), grep], [grep], "final"])
    full = [m for m in client.seen[1][0] if m["role"] == "tool"][0]["content"]
    stub = [m for m in client.seen[2][0] if m["role"] == "tool"][0]["content"]
    delta = client.count_tokens(full) - client.count_tokens(stub)
    assert delta > 0 and res.dropped_tokens == delta
    assert res.drop_saved_tokens == 2 * delta          # requests 3 and 4 were each that much smaller
    _, plain = _sel([[read], "final"])
    assert plain.dropped_tokens == 0 and plain.drop_saved_tokens == 0
