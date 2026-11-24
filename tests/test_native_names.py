"""The native-names arm: Qwen Code's tool names and parameters, translated to the harness's tools."""
from context_bench.levels import l7_agentic as A
from context_bench.types import RunConfig


def _names(variant, tool_names):
    return [s["function"]["name"] for s in A.tool_schemas(variant, 4, tool_names)]


def test_schemas_use_qwen_names_and_parameters():
    assert _names("selective", "qwen") == [A.public_name(n, "qwen") for n in A._variant_tools("selective", 4)]
    assert "grep_search" in _names("selective", "qwen") and "grep" not in _names("selective", "qwen")
    assert "write_file" in _names("edit", "qwen") and "create_file" not in _names("edit", "qwen")
    fns = {s["function"]["name"]: s["function"] for s in A.tool_schemas("selective", 4, "qwen")}
    assert set(fns["read_file"]["parameters"]["properties"]) == {"file_path", "offset", "limit"}
    assert set(fns["grep_search"]["parameters"]["properties"]) >= {"pattern", "glob"}
    edit = {s["function"]["name"]: s["function"] for s in A.tool_schemas("edit", 4, "qwen")}["edit"]
    assert edit["parameters"]["required"] == ["file_path", "old_string", "new_string"]
    assert "old_string must match" in edit["description"]
    assert _names("selective", "harness") == A._variant_tools("selective", 4)      # default unchanged


def test_calls_translate_to_the_harness_tools():
    assert A._internal_name("grep_search", "selective", 4, "qwen") == "grep"
    assert A._internal_name("grep", "selective", 4, "qwen") is None            # the harness name is unknown here
    assert A._internal_name("read_symbol", "selective", 4, "qwen") == "read_symbol"
    assert A._internal_args("read_file", {"file_path": "a.py", "offset": 10, "limit": 5}, "qwen") == \
        {"path": "a.py", "start": 11, "end": 15}
    assert A._internal_args("read_file", {"file_path": "a.py"}, "qwen") == {"path": "a.py"}
    assert A._internal_args("edit", {"file_path": "a.py", "old_string": "x", "new_string": "y"}, "qwen") == \
        {"path": "a.py", "old": "x", "new": "y"}
    assert A._internal_args("grep", {"pattern": "p", "glob": "*.py"}, "qwen") == {"pattern": "p", "path_glob": "*.py"}


def test_usage_and_note_speak_qwen():
    assert "file_path" in A._usage("read_file", "selective", 4, "qwen")
    assert "grep_search" in A._usage("grep", "selective", 4, "qwen")             # unknown: lists the real tools
    note = A._qwen_note(A.TOOLS_NOTE_EDIT)
    assert "write_file" in note and "create_file" not in note and "- grep_search:" in note


def test_prompt_and_run_ids():
    from context_bench.prompt import render
    from context_bench.types import ContextBlock, Task
    from pathlib import Path
    t = Task(id="x", category="control", prompt="Do it.", target_file="a.py", needed_helpers=(),
             hidden_tests=Path("."), tier=1)
    sys_q, user_q = (m["content"] for m in render(t, ContextBlock(text="c"), "edit_tools", 4, "qwen"))
    assert "write_file" in sys_q + user_q and "create_file" not in sys_q + user_q
    rc = RunConfig("reuse-001", "L7", "m", 1, agent_variant="selective")
    assert rc.run_id == RunConfig("reuse-001", "L7", "m", 1, agent_variant="selective", tool_names="harness").run_id
    assert rc.run_id != RunConfig("reuse-001", "L7", "m", 1, agent_variant="selective", tool_names="qwen").run_id


def test_agent_loop_runs_qwen_named_calls_and_rejects_harness_names():
    import json
    from test_l7_agentic import M, FakeClient
    from context_bench.levels.l7_agentic import RepoTools, run_agent
    from context_bench.retrieval.bm25 import BM25Retriever
    client = FakeClient([[("read_file", {"file_path": "shopkit/money.py", "offset": 0, "limit": 3}),
                          ("grep_search", {"pattern": "def format_money", "glob": "shopkit/**/*.py"}),
                          ("grep", {"pattern": "x"})],
                         "done"])
    res = run_agent(client, [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
                    RepoTools(M, make_retriever=BM25Retriever, rich=True), agent_variant="selective",
                    temperature=None, seed=1, max_tokens=100, context_window=100_000, failed_call_cost=0.5,
                    tool_names="qwen")
    tool_msgs = [m["content"] for m in res.transcript if m.get("role") == "tool"]
    assert "File: shopkit/money.py (lines 1-3 of" in tool_msgs[0]           # offset 0, limit 3 -> lines 1-3
    assert "grep_search" in tool_msgs[1] and "format_money" in tool_msgs[1]   # result labelled in Qwen's name
    assert "unknown tool 'grep'" in tool_msgs[2] and "grep_search" in tool_msgs[2]
    offered = [t["function"]["name"] for t in client.seen[0][1]]
    assert "grep_search" in offered and "grep" not in offered and res.tool_errors == 1
