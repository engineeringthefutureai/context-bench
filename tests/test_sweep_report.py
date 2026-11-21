"""cbench run (configs + sweep) and cbench report, with a fake model client."""
import httpx

from context_bench import results
from test_pieces5_8 import FakeClient, _experiment

REPLY = '<file path="shopkit/utils/units.py">\ndef parse_weight(text):\n    return 0\n</file>'


def test_configs_expand_and_filter(tmp_path):
    exp = _experiment(tmp_path, FakeClient(REPLY))
    rcs = exp.configs()
    l0 = [rc for rc in rcs if rc.level == "L0"]
    assert [rc.task_id for rc in l0] == ["control-001"]          # categories/tiers filter
    assert sum(rc.level == "L1" and rc.prompt_arm == "base" for rc in rcs) == len(exp.tasks)
    assert {rc.level for rc in exp.configs(["L1", "L3"])} == {"L1", "L3"}
    assert len({rc.run_id for rc in rcs}) == len(rcs)            # no duplicate configs


def test_sweep_resumes_skips_unimplemented_and_retries_server_errors(tmp_path):
    client = FakeClient(REPLY)
    exp = _experiment(tmp_path, client)
    exp.sweep(["L0", "L4"])                                       # L4 isn't implemented yet: skipped
    rows = results.load(exp.results_path)
    assert [(r["task_id"], r["level"]) for r in rows] == [("control-001", "L0")]
    exp.sweep(["L0"])                                             # already done: no new call
    assert len(client.calls) == 1

    def boom(messages, **kw):
        raise httpx.ReadTimeout("timed out")
    client.chat = boom
    exp.sweep(["L1"])                                             # server errors aren't recorded
    assert len(results.load(exp.results_path)) == 1


def test_report(tmp_path):
    exp = _experiment(tmp_path, FakeClient(REPLY))
    exp.sweep(["L0", "L1"])
    text = results.report(exp.results_path)
    assert "| control-001 (1) | ❌ 1/13 |" in text and "## Per configuration" in text
    assert "| L1 nothink | 6 |" in text and "| L1+hint nothink | 6 |" in text   # config: thinking off; --level L1 includes the hint arm
    assert "helper not in context" in text
    assert results.report(tmp_path / "missing.jsonl").startswith("No results")


def test_sweep_spreads_runs_over_base_urls(tmp_path):
    from context_bench.runner import Experiment
    exp = _experiment(tmp_path, FakeClient(REPLY))
    exp.config["models"][exp.default_model]["base_urls"] = ["http://a", "http://b"]
    used = []
    def factory(m, url):
        used.append(url)
        return FakeClient(REPLY)
    exp._client_factory = factory
    exp.sweep(["L1"])
    rows = results.load(exp.results_path)
    assert len(rows) == 2 * len(exp.tasks) and sorted(used) == ["http://a", "http://b"]


def test_busy_seconds_counts_overlap_once():
    from context_bench.results import _busy_seconds
    t = lambda s: f"2026-11-20T10:00:{s:02d}+00:00"
    rows = [{"started_at": t(0), "finished_at": t(10)}, {"started_at": t(5), "finished_at": t(20)},
            {"started_at": t(30), "finished_at": t(40)}, {"started_at": None, "finished_at": None}]
    assert _busy_seconds(rows) == 30.0


def test_run_records_perf_fields(tmp_path):
    exp = _experiment(tmp_path, FakeClient(REPLY))
    exp.sweep(["L0"])
    r = results.load(exp.results_path)[0]
    assert r["concurrency"] == 1 and r["backend"] == "unknown" and r["started_at"] <= r["finished_at"]
    assert "## Performance per model" in results.report(exp.results_path)


def test_thinking_off_is_a_separate_run_and_reaches_the_request(tmp_path):
    from context_bench.types import RunConfig
    import hashlib, json as _json
    from dataclasses import asdict
    on = RunConfig("t", "L1", "m", 1)
    old = {k: v for k, v in asdict(on).items() if k not in ("thinking", "map_docs")}  # ids before these fields
    assert on.run_id == hashlib.sha256(_json.dumps(old, sort_keys=True).encode()).hexdigest()[:16]
    assert RunConfig("t", "L1", "m", 1, thinking=False).run_id != on.run_id
    client = FakeClient(REPLY)
    exp = _experiment(tmp_path, client)
    exp.sweep(["L0"], thinking=False)
    assert client.calls[-1][1].get("thinking") is False
    r = results.load(exp.results_path)[0]
    assert r["thinking"] is False and "L0 nothink" in results.report(exp.results_path)


def test_chat_body_disables_thinking():
    import json as _json
    from context_bench.llm import OpenAICompatClient
    seen = []
    def handler(req):
        seen.append(_json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}],
                                         "usage": {}})
    c = OpenAICompatClient("http://srv", "m", api_key="k", transport=httpx.MockTransport(handler))
    c.chat([], temperature=None, seed=1, max_tokens=5, thinking=False)
    c.chat([], temperature=None, seed=1, max_tokens=5)
    assert seen[0]["chat_template_kwargs"] == {"enable_thinking": False} and "chat_template_kwargs" not in seen[1]


def test_changed_repo_reruns(tmp_path):
    import shutil
    from test_pieces5_8 import FIXTURE
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo, ignore=shutil.ignore_patterns("__pycache__"))
    client = FakeClient(REPLY)
    exp = _experiment(tmp_path, client)
    exp.config["repos"]["S"] = str(repo)
    exp.sweep(["L0"])
    exp.sweep(["L0"])
    assert len(client.calls) == 1                      # unchanged repo: resumed, nothing rerun
    (repo / "shopkit" / "extra.py").write_text("X = 1\n")
    exp._repo_hash.clear()
    exp.sweep(["L0"])
    assert len(client.calls) == 2                      # changed repo: rerun
    rows = results.load(exp.results_path)
    assert len(rows) == 1 and rows[0]["repo_hash"] == exp.repo_hash("S")   # newest row wins


def test_thinking_default_from_config_and_override(tmp_path):
    exp = _experiment(tmp_path, FakeClient(REPLY))
    exp.config["generation"]["thinking"] = False
    assert {rc.thinking for rc in exp.configs(["L1"])} == {False}
    assert {rc.thinking for rc in exp.configs(["L1"], thinking=True)} == {True}
    exp.config["generation"]["thinking"] = True
    assert {rc.thinking for rc in exp.configs(["L1"])} == {True}
