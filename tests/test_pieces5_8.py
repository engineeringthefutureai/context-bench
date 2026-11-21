import json
import subprocess
import tempfile
from pathlib import Path

import httpx
import pytest

from context_bench import results
from context_bench.apply import make_sandbox
from context_bench.levels.l1_one_file import build_context as l1
from context_bench.llm import ChatResult, OpenAICompatClient, api_key_from_env
from context_bench.runner import Experiment
from context_bench.tasks import load_tasks
from context_bench.types import RunConfig
from context_bench.workspace import load_workspace

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "repo_S"
TASKS = load_tasks(ROOT / "tasks")


# ---- llm client
def _mock_client(handler):
    return OpenAICompatClient("http://srv", "m", api_key="k", transport=httpx.MockTransport(handler))


def test_chat_request_and_parsing():
    seen = []
    def handler(req):
        seen.append((req.headers["authorization"], json.loads(req.content)))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": "hi", "reasoning_content": "hmm", "tool_calls": [{"id": "1"}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3}})
    c = _mock_client(handler)
    r = c.chat([{"role": "user", "content": "x"}], temperature=None, seed=1, max_tokens=50)
    assert (r.content, r.reasoning, r.tool_calls, r.tokens_in, r.tokens_out, r.finish_reason) == \
        ("hi", "hmm", [{"id": "1"}], 10, 3, "stop")
    auth, body = seen[0]
    assert auth == "Bearer k" and "temperature" not in body and body["seed"] == 1 and "tools" not in body
    c.chat([], temperature=0.2, seed=1, max_tokens=5, tools=[{"t": 1}])
    assert seen[1][1]["temperature"] == 0.2 and seen[1][1]["tools"] == [{"t": 1}]


def test_sampling_and_token_counting():
    def handler(req):
        if req.url.path == "/props":
            return httpx.Response(200, json={"default_generation_settings": {"params": {
                "temperature": 1.0, "top_k": 20, "n_predict": -1}}})
        if req.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "abcd"})
        if req.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": list(json.loads(req.content)["content"])})
        return httpx.Response(404)
    c = _mock_client(handler)
    assert c.server_sampling() == {"temperature": 1.0, "top_k": 20}
    assert c.count_prompt_tokens([{"role": "user", "content": "ignored"}]) == 4
    dead = _mock_client(lambda req: httpx.Response(404))
    assert dead.server_sampling() == {} and dead.count_tokens("x" * 30) == 11   # estimate fallback


def test_api_key_resolution(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=1\nCBENCH_API_KEY=from-file\n")
    monkeypatch.delenv("CBENCH_API_KEY", raising=False)
    assert api_key_from_env(env) == "from-file"
    monkeypatch.setenv("CBENCH_API_KEY", "from-env")
    assert api_key_from_env(env) == "from-env"
    monkeypatch.delenv("CBENCH_API_KEY")
    assert api_key_from_env(tmp_path / "missing") is None


# ---- L1, results
def test_l1_context():
    ws = load_workspace(FIXTURE)
    ctx = l1(TASKS["reuse-001"], ws)
    assert ctx.text.startswith("File: shopkit/orders/export.py\n```python\nimport json") and ctx.included
    assert "does not exist yet" in l1(TASKS["control-001"], ws).text


def test_results_append_and_resume(tmp_path):
    path = tmp_path / "r" / "results.jsonl"
    assert results.done_run_ids(path) == set()
    exp_result = _run(tmp_path, "reuse-001", _reference_reply("reuse-001", "shopkit/orders/export.py"))
    results.append(path, exp_result)
    assert results.done_run_ids(path) == {exp_result.run_id}


# ---- runner end to end with a fake model
class FakeClient:
    def __init__(self, reply, prompt_tokens=500):
        self.reply, self.prompt_tokens, self.calls = reply, prompt_tokens, []
    def server_sampling(self):
        return {"temperature": 1.0}
    def count_prompt_tokens(self, messages):
        return self.prompt_tokens
    def chat(self, messages, **kw):
        self.calls.append((messages, kw))
        return ChatResult(content=self.reply, reasoning="thinking...", tokens_in=520, tokens_out=300,
                          latency_s=1.5, finish_reason="stop")


def _reference_reply(task, path):
    box = make_sandbox(FIXTURE, Path(tempfile.mkdtemp()) / "b")
    subprocess.run(["patch", "-p1", "-s"], cwd=box, check=True,
                   input=(ROOT / "tasks" / task / "solutions" / "reference.diff").read_text(), text=True)
    return f'Here you go.\n<file path="{path}">\n{(box / path).read_text()}</file>'


def _experiment(tmp_path, client):
    cfg = (ROOT / "config" / "experiment.toml").read_text()
    cfg = (cfg.replace('tasks = "tasks"', f'tasks = "{ROOT / "tasks"}"')
              .replace('results = "results/results.jsonl"', f'results = "{tmp_path / "results.jsonl"}"')
              .replace('runs = "results/runs"', f'runs = "{tmp_path / "runs"}"')
              .replace('S = "fixtures/repo_S"', f'S = "{FIXTURE}"')
              .replace('M = "fixtures/repo_M"', f'M = "{ROOT / "fixtures" / "repo_M"}"')
              .replace('base_url = "http://localhost:11436"', 'base_url = "http://127.0.0.1:9"'))   # never the real embedder
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "experiment.toml").write_text(cfg)
    return Experiment(tmp_path / "config" / "experiment.toml", client_factory=lambda m, url: client)


def _run(tmp_path, task, reply, prompt_tokens=500, level="L1"):
    client = FakeClient(reply, prompt_tokens)
    exp = _experiment(tmp_path, client)
    return exp.run_one(RunConfig(task_id=task, level=level, model=exp.default_model, seed=1))


def test_run_one_reference_is_ok_and_bundle_written(tmp_path):
    r = _run(tmp_path, "reuse-001", _reference_reply("reuse-001", "shopkit/orders/export.py"))
    assert (r.label, r.tests_passed, r.tests_total, r.reuse_passed, r.ctx_fits) == ("ok", 2, 2, True, True)
    assert r.helper_in_context is False            # L1 shows only export.py, not the helpers
    assert (r.tokens_in, r.tokens_out, r.peak_ctx_tokens, r.context_window) == (520, 300, 520, 131072)
    assert r.sampling == {"temperature": 1.0} and r.error is None and r.tier == 4
    run_dir = tmp_path / "runs" / r.run_id
    assert {p.name for p in run_dir.iterdir()} == {
        "patch.diff", "prompt.json", "response.txt", "reasoning.txt", "tests.txt", "run.json"}
    assert "+def export_orders_csv" in (run_dir / "patch.diff").read_text()


def test_run_one_garbage_reply_is_broken(tmp_path):
    r = _run(tmp_path, "reuse-001", "I can't do that.")
    assert (r.label, r.patch_path, r.error) == ("broken", None, "no <file> blocks in the response")
    assert r.reuse_passed is False


def test_run_one_no_fit_skips_the_model(tmp_path):
    r = _run(tmp_path, "reuse-001", "unused", prompt_tokens=131072 - 16384 + 1)
    assert (r.label, r.ctx_fits, r.tests_total) == ("no_fit", False, 0) and "window" in r.error


def test_run_one_unsafe_path_is_broken(tmp_path):
    r = _run(tmp_path, "control-001", '<file path="../evil.py">x = 1\n</file>')
    assert r.label == "broken" and r.error.startswith("unsafe path")
