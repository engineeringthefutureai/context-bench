"""L-AG (the Antigravity SDK harness as the solver, in a container): prompt, confinement, config, and what is
collected from a session. No model calls and no container: the session is faked, the config is built with the
real SDK (skipped without it), compose.yaml is read as text."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from context_bench.levels import LEVELS
from context_bench.levels import l_ag_antigravity as ag
from context_bench.runner import _edits_in_place
from context_bench.types import Task

ROOT = Path(__file__).resolve().parents[1]


def _solve():
    spec = importlib.util.spec_from_file_location("l_ag_solve", ROOT / "docker" / "l-ag" / "solve.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _task() -> Task:
    return Task(id="t", category="reuse", tier=3, prompt="Add a weight label to the packing slip.",
                target_file="shopkit/x.py", needed_helpers=(), hidden_tests=Path("/nonexistent"))


def test_registered_and_builds_no_context():
    assert "L-AG" in LEVELS
    assert LEVELS["L-AG"](_task(), None).text == ""


def test_user_prompt_is_the_task_and_where_the_repo_is():
    text = ag.user_prompt(_task())
    assert text.startswith("Add a weight label to the packing slip.") and "/workspace" in text
    for word in ("reuse", "helper", "test", "legacy", "dead"):
        assert word not in text.lower()


def test_upstream_url():
    assert ag.upstream_url("http://localhost:8080") == "http://host.docker.internal:8080"
    assert ag.upstream_url("http://127.0.0.1:8080/v1/") == "http://host.docker.internal:8080"
    assert ag.upstream_url("http://gpu-box:8080") == "http://gpu-box:8080"


def test_edits_in_place():
    assert _edits_in_place("edit_tools", "L8") and _edits_in_place("base", "L-AG")
    assert not _edits_in_place("base", "L7")


def test_compose_mounts_only_the_workspace_and_out():
    text = (ROOT / "docker" / "l-ag" / "compose.yaml").read_text()
    mounts = [l.strip() for l in text.splitlines() if l.strip().startswith('- "${L_AG_')]
    assert mounts == ['- "${L_AG_WORKSPACE:?}:/workspace"', '- "${L_AG_OUT:?}:/out"',
                      '- "${L_AG_USAGE:?}:/usage"          # per-call token usage; the agent\'s container doesn\'t mount it']
    for setting in ("read_only: true", "cap_drop: [ALL]", "no-new-privileges:true", 'user: "${L_AG_UID:?}'):
        assert setting in text
    assert "network_mode" not in text and "privileged" not in text and "docker.sock" not in text


def test_image_holds_nothing_of_the_project():
    copies = [l for l in (ROOT / "docker" / "l-ag" / "Dockerfile").read_text().splitlines()
              if l.startswith(("COPY", "ADD"))]
    assert copies == ["COPY solve.py model_proxy.py /opt/l-ag/"]
    for name in ("solve.py", "model_proxy.py"):                # standalone: no project imports
        src = (ROOT / "docker" / "l-ag" / name).read_text()
        assert "context_bench" not in src.split('"""', 2)[2]


def test_compose_config_resolves(tmp_path):
    env = {"L_AG_WORKSPACE": str(tmp_path), "L_AG_OUT": str(tmp_path), "L_AG_USAGE": str(tmp_path),
           "L_AG_MODEL": "m", "L_AG_UID": "1000",
           "L_AG_GID": "1000", "PATH": "/usr/bin:/bin:/usr/local/bin"}
    try:
        out = subprocess.run([*ag.COMPOSE, "config", "--format", "json"], env=env, capture_output=True, text=True,
                             timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("docker compose not available")
    if out.returncode:
        pytest.skip(f"docker compose config failed: {out.stderr[:200]}")
    svc = json.loads(out.stdout)["services"]["l-ag"]
    assert sorted(v["target"] for v in svc["volumes"]) == ["/out", "/workspace"]
    assert svc["environment"]["L_AG_BASE_URL"] == "http://model-proxy:8080/v1"
    assert "L_AG_PROXY_KEY" not in svc["environment"]           # the key stays in the proxy's container
    proxy = json.loads(out.stdout)["services"]["model-proxy"]
    assert [v["target"] for v in proxy["volumes"]] == ["/usage"]   # its usage log only; never the workspace


def test_config_blind_and_autonomous(tmp_path):
    pytest.importorskip("google.antigravity")
    from google.antigravity import types
    solve = _solve()
    (tmp_path / "home").mkdir()
    cfg = solve.config(base_url="http://host.docker.internal:8080/v1", model="m", home=tmp_path,
                       max_model_calls=7, command_timeout_s=60)
    assert cfg.workspaces == ["/workspace"]
    assert cfg.env["HOME"] == str(tmp_path / "home")
    off = set(cfg.capabilities.disabled_tools)
    for t in (types.BuiltinTools.SEARCH_WEB, types.BuiltinTools.READ_URL_CONTENT, types.BuiltinTools.ASK_QUESTION,
              types.BuiltinTools.INVOKE_SUBAGENT):
        assert t in off
    assert types.BuiltinTools.RUN_COMMAND not in off and types.BuiltinTools.EDIT_FILE not in off
    assert cfg.capabilities.enable_subagents is False
    assert cfg.budget_config.max_model_calls == 7


def _step(type_, content="", thinking="", tool_calls=(), error="", prompt=None, id="", status="DONE"):
    usage = SimpleNamespace(prompt_token_count=prompt) if prompt is not None else None
    return SimpleNamespace(id=id, type=type_, source="MODEL", status=status, content=content, thinking=thinking,
                           tool_calls=[SimpleNamespace(name=n, args=a) for n, a in tool_calls], error=error,
                           usage_metadata=usage)


def _conv(steps, stop="UNSPECIFIED"):
    return SimpleNamespace(
        history=steps, _last_turn_stop_reason=stop,
        total_usage=SimpleNamespace(prompt_token_count=9000, cached_content_token_count=6000,
                                    candidates_token_count=300, thoughts_token_count=700))


def test_collect_counts_tools_tokens_and_calls():
    got = _solve().collect(_conv([
        # status snapshots of one step: only the last one counts
        _step("TOOL_CALL", content="View a.py", tool_calls=[("view_file", {})], id="s1", status="ACTIVE"),
        _step("TOOL_CALL", content="View a.py", tool_calls=[("view_file", {})], id="s1", status="ACTIVE"),
        _step("TOOL_CALL", content="View a.py", thinking="look first", id="s1",
              tool_calls=[("view_file", {"AbsolutePath": "/workspace/a.py"})], prompt=2000),
        _step("TOOL_CALL", tool_calls=[("run_command", {"CommandLine": "pytest -q"}),
                                       ("view_file", {"AbsolutePath": "/workspace/b.py"})], error="exit 1", prompt=3500,
              id="s2"),
        _step("TEXT_RESPONSE", content="Done.", prompt=3500, id="s3"),
    ]), [{"name": "view_file", "result": "def format_weight(grams, unit):", "error": None}])
    run = ag.AgRun(**got)
    assert run.tool_counts == {"view_file": 2, "run_command": 1} and run.tool_calls == 3 and run.tool_errors == 1
    assert run.model_calls == 3 and run.peak_ctx_tokens == 3500
    assert (run.tokens_in, run.tokens_cached, run.tokens_out, run.tokens_thinking) == (9000, 6000, 1000, 700)
    assert "def format_weight" in run.tool_output and run.reasoning == "look first"
    assert run.stop_reason is None
    assert run.transcript[1]["tool_calls"][0] == {"name": "run_command", "args": {"CommandLine": "pytest -q"}}
    assert run.transcript[-1]["type"] == "TOOL_RESULTS" and len(run.transcript) == 4
    json.dumps(got)                                    # it travels as result.json


def test_collect_records_a_harness_stop():
    got = _solve().collect(_conv([_step("TEXT_RESPONSE", content="...", prompt=10)], stop="MAX_MODEL_CALLS_EXCEEDED"))
    assert got["stop_reason"] == "MAX_MODEL_CALLS_EXCEEDED"


def test_run_ag_reads_the_result(tmp_path, monkeypatch):
    """run_ag: the prompt goes in /out, the container's result.json comes back as an AgRun."""
    seen = {}

    def fake_run(cmd, env, **kw):
        if "down" in cmd:
            seen["down"] = cmd
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        out = Path(env["L_AG_OUT"])
        seen.update(cmd=cmd, prompt=(out / "prompt.txt").read_text(), env=env)
        (out / "result.json").write_text(json.dumps({"content": "Done.", "tool_calls": 4, "unknown_field": 1}))
        (Path(env["L_AG_USAGE"]) / "calls.jsonl").write_text(
            json.dumps({"prompt": 1000, "cached": 0, "output": 50, "thinking": 10}) + "\n"
            + json.dumps({"prompt": 3000, "cached": 900, "output": 70, "thinking": 0}) + "\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ag.subprocess, "run", fake_run)
    run = ag.run_ag(_task(), tmp_path, base_url="http://localhost:8080", model="Qwen/Qwen3.8-27B")
    assert run.content == "Done." and run.tool_calls == 4
    assert (run.model_calls, run.peak_ctx_tokens, run.tokens_in, run.tokens_cached, run.tokens_out) == (2, 3000, 4000, 900, 130)
    assert seen["prompt"] == ag.user_prompt(_task(), tmp_path)
    assert seen["env"]["L_AG_WORKSPACE"] == str(tmp_path.resolve())
    assert seen["env"]["L_AG_PROXY_UPSTREAM"] == "http://host.docker.internal:8080"
    assert seen["cmd"][-1] == "l-ag" and "--rm" in seen["cmd"]
    assert seen["down"][-4:] == ["down", "--remove-orphans", "--timeout", "5"]   # proxy taken down too


def test_run_ag_without_result_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(ag.subprocess, "run", lambda cmd, env, **kw: SimpleNamespace(returncode=1, stdout="",
                                                                                   stderr="boom"))
    run = ag.run_ag(_task(), tmp_path, base_url="http://localhost:8080", model="m")
    assert run.finish_reason == "error" and "boom" in run.error


def test_proxy_env_per_api(monkeypatch):
    import context_bench.llm as llm
    monkeypatch.setattr(llm, "env_value", lambda name, *a: {"GEMINI_API_KEY": "g-key", "CBENCH_API_KEY": "v-key"}.get(name))
    g = ag.proxy_env("gemini", "https://generativelanguage.googleapis.com")
    assert g == {"L_AG_API": "gemini", "L_AG_PROXY_UPSTREAM": "https://generativelanguage.googleapis.com",
                 "L_AG_PROXY_AUTH": "goog", "L_AG_PROXY_KEY": "g-key"}
    o = ag.proxy_env("openai", "http://localhost:8080")
    assert o["L_AG_PROXY_AUTH"] == "bearer" and o["L_AG_PROXY_KEY"] == "v-key"
    assert o["L_AG_PROXY_UPSTREAM"] == "http://host.docker.internal:8080"
    monkeypatch.setattr(llm, "env_value", lambda name, *a: None)
    with pytest.raises(RuntimeError):
        ag.proxy_env("gemini", "https://generativelanguage.googleapis.com")


def test_model_proxy_drops_the_clients_key(monkeypatch):
    spec = importlib.util.spec_from_file_location("l_ag_proxy", ROOT / "docker" / "l-ag" / "model_proxy.py")
    proxy = importlib.util.module_from_spec(spec)
    monkeypatch.setenv("L_AG_PROXY_UPSTREAM", "https://example.test/base")
    monkeypatch.setenv("L_AG_PROXY_AUTH", "goog")
    monkeypatch.setenv("L_AG_PROXY_KEY", "real")
    spec.loader.exec_module(proxy)
    assert proxy.upstream_path("/v1beta/models/m:streamGenerateContent?alt=sse&key=fake") == \
        "/base/v1beta/models/m:streamGenerateContent?alt=sse"
    assert proxy.auth_headers() == {"x-goog-api-key": "real"}
    assert {"authorization", "x-goog-api-key"} <= proxy.DROP


def test_seen_text_from_step_args(tmp_path):
    (tmp_path / "shopkit").mkdir()
    (tmp_path / "shopkit" / "money.py").write_text("import x\n\ndef format_money(cents, cur):\n    return ''\n")
    (tmp_path.parent / "secret.py").write_text("def outside(): pass\n")
    run = ag.AgRun(transcript=[
        {"type": "TOOL_CALL", "tool_calls": [{"name": "view_file", "args": {
            "file_path": "/workspace/shopkit/money.py", "start_line": 2, "end_line": 3}}]},
        {"type": "TOOL_CALL", "tool_calls": [{"name": "run_command", "args": {
            "command_line": "grep -rn sku", "combined_output": "shopkit/utils/text.py:3:def normalize_sku(s):"}}]},
        {"type": "TOOL_CALL", "tool_calls": [{"name": "view_file", "args": {"file_path": "/workspace/../secret.py"}}]},
    ])
    text = ag.seen_text(run, tmp_path)
    assert "def format_money(cents, cur):" in text and "import x" not in text
    assert "def normalize_sku(s):" in text
    assert "outside" not in text                       # never reads outside the repository


def test_quota_errors_are_not_results():
    assert ag.quota_error(ag.AgRun(error="AntigravityExecutionError: Error 429, Message: You exceeded your current quota"))
    assert ag.quota_error(ag.AgRun(stop_reason="QUOTA_EXHAUSTED"))
    assert not ag.quota_error(ag.AgRun(error="timed out after 3600 s"))
    assert not ag.quota_error(ag.AgRun(stop_reason="MAX_MODEL_CALLS_EXCEEDED"))


def test_config_waits_out_rate_limits(tmp_path):
    pytest.importorskip("google.antigravity")
    cfg = _solve().config(base_url="http://model-proxy:8080/v1", model="m", home=tmp_path, max_model_calls=5,
                          command_timeout_s=60)
    assert cfg.retry_config.api_retry.max_retries > 1000


def test_proxy_throttle_keeps_a_sliding_minute():
    spec = importlib.util.spec_from_file_location("l_ag_proxy2", ROOT / "docker" / "l-ag" / "model_proxy.py")
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    now = [0.0]
    t = proxy.Throttle(3, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
    assert [t.wait() for _ in range(3)] == [0.0, 0.0, 0.0]      # three at t=0
    waited = t.wait()                                         # the fourth waits for the first to age out
    assert 60 <= waited < 61 and now[0] >= 60
    assert proxy.Throttle(0).wait() == 0.0                     # 0 = no limit
    now[0] = 0.0
    tt = proxy.Throttle(0, tpm=100, clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
    assert tt.wait(60) == 0.0 and 60 <= tt.wait(60) < 61       # the second would make 120 tokens in a minute
    assert tt.wait(500) >= 60                                  # larger than the limit: goes alone


def test_quota_detail_names_the_metric():
    run = ag.AgRun(error="Error 429 ... * Quota exceeded for metric: generativelanguage.googleapis.com/"
                         "generate_content_free_tier_input_token_count, limit: 250000, model: gemini-3.5-flash-lite\n...")
    assert ag.quota_detail(run).endswith("limit: 250000, model: gemini-3.5-flash-lite")


def test_proxy_reads_usage_from_responses():
    spec = importlib.util.spec_from_file_location("l_ag_proxy3", ROOT / "docker" / "l-ag" / "model_proxy.py")
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    sse = (b'data: {"candidates": []}\n\n'
           b'data: {"candidates": [], "usageMetadata": {"promptTokenCount": 12000, "cachedContentTokenCount": 8000, '
           b'"candidatesTokenCount": 40, "thoughtsTokenCount": 5}}\n\n')
    assert proxy.usage_of(sse) == {"prompt": 12000, "cached": 8000, "output": 40, "thinking": 5}
    openai = json.dumps({"usage": {"prompt_tokens": 900, "completion_tokens": 30,
                                   "prompt_tokens_details": {"cached_tokens": 512}}}).encode()
    assert proxy.usage_of(openai) == {"prompt": 900, "output": 30, "cached": 512, "thinking": None}
    assert proxy.usage_of(b"not json") is None


def test_percentiles_nearest_rank():
    v = list(range(1, 101))
    assert ag.percentiles(v) == {"p50": 50, "p95": 95, "p98": 98, "p100": 100}
    assert ag.percentiles([7]) == {"p50": 7, "p95": 7, "p98": 7, "p100": 7}
    assert ag.percentiles([]) == {}


def test_proxy_thinking_switch():
    spec = importlib.util.spec_from_file_location("l_ag_proxy4", ROOT / "docker" / "l-ag" / "model_proxy.py")
    proxy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(proxy)
    chat = json.dumps({"model": "q", "messages": [], "chat_template_kwargs": {"x": 1}, "reasoning_effort": "high"}).encode()
    out, seen = proxy.apply_thinking(chat, "off")
    doc = json.loads(out)
    assert doc["chat_template_kwargs"] == {"x": 1, "enable_thinking": False}     # overrides, keeps the rest
    assert seen["reasoning_effort"] == "high" and seen["keys"] == sorted(json.loads(chat))
    assert json.loads(proxy.apply_thinking(chat, "on")[0])["chat_template_kwargs"]["enable_thinking"] is True
    assert proxy.apply_thinking(chat, "")[0] == chat                           # unset: forwarded as sent
    gemini = json.dumps({"contents": []}).encode()
    assert proxy.apply_thinking(gemini, "off") == (gemini, {})                # not an OpenAI chat request


def test_run_ag_passes_thinking(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, env, **kw):
        if "down" not in cmd:
            seen["thinking"] = env["L_AG_PROXY_THINKING"]
        return SimpleNamespace(returncode=1, stdout="", stderr="x")

    monkeypatch.setattr(ag.subprocess, "run", fake_run)
    ag.run_ag(_task(), tmp_path, base_url="http://localhost:8080", model="m", thinking=False)
    assert seen["thinking"] == "off"
    ag.run_ag(_task(), tmp_path, base_url="http://localhost:8080", model="m")
    assert seen["thinking"] == ""


def test_model_server_errors_are_not_results():
    assert ag.server_error(ag.AgRun(finish_reason="error", error="AntigravityExecutionError: dial tcp: connection refused"))
    assert ag.server_error(ag.AgRun(finish_reason="error", error="Error 503, Message: Service Unavailable"))
    assert not ag.server_error(ag.AgRun(finish_reason="timeout", error="timed out after 3600 s"))
    assert not ag.server_error(ag.AgRun(finish_reason="harness_stop", error="stopped by the harness: MAX_MODEL_CALLS_EXCEEDED"))


def test_prompt_names_a_file_the_change_creates(tmp_path):
    """Like every other level: a target that doesn't exist yet is named, as "File: path / (This file does not
    exist yet.)"; an existing target is not."""
    t = Task(id="control-001", category="control", tier=1, prompt="Create parse_weight(text).",
             target_file="shopkit/utils/units.py", needed_helpers=(), hidden_tests=Path("/nonexistent"))
    text = ag.user_prompt(t, tmp_path)
    assert "File: shopkit/utils/units.py\n(This file does not exist yet.)" in text
    assert text.index("Create parse_weight") < text.index("File:") < text.index("/workspace")
    (tmp_path / "shopkit/utils").mkdir(parents=True)
    (tmp_path / "shopkit/utils/units.py").write_text("")
    assert "File:" not in ag.user_prompt(t, tmp_path)
