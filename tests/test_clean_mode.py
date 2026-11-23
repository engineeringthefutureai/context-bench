"""cbench run --clean: serial, model server restarted before every run (fake command + client)."""
import numpy as np

from context_bench import results
from test_pieces5_8 import FakeClient, _experiment

REPLY = '<file path="shopkit/utils/units.py">\ndef parse_weight(text):\n    return 0\n</file>'


class ReadyClient(FakeClient):
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.probes = 0

    def ready(self):
        self.probes += 1
        return self.probes > 1          # not ready on the first probe, ready after


def _clean_exp(tmp_path, monkeypatch, cmd):
    client = ReadyClient(REPLY)
    exp = _experiment(tmp_path, client)
    exp.config["models"][exp.default_model]["base_urls"] = ["http://a", "http://b"]
    urls = []
    def factory(m, url):
        urls.append(url)
        return client
    exp._client_factory = factory
    monkeypatch.setenv("CBENCH_RESTART_CMD", cmd)
    monkeypatch.setenv("CBENCH_READY_TIMEOUT", "30")
    monkeypatch.setattr("context_bench.runner.time.sleep", lambda s: None)
    return exp, client, urls


def test_clean_restarts_before_every_run_serially(tmp_path, monkeypatch):
    marker = tmp_path / "restarts.log"
    exp, client, urls = _clean_exp(tmp_path, monkeypatch, f"echo restart >> {marker}")
    from test_sweep_report import _unimplemented_level
    _unimplemented_level(exp, monkeypatch)
    exp.sweep(["L0", "L9"], clean=True, task_ids=["control-001"])   # L9: no restart spent on it
    rows = results.load(exp.results_path)
    assert len(rows) == 1 and rows[0]["cold_start"] is True and rows[0]["concurrency"] == 1
    assert marker.read_text().count("restart") == 1
    assert set(urls) == {"http://a"}              # first server only
    warmup = [kw for m, kw in client.calls if m[0]["content"] == "Reply with OK."]
    assert len(warmup) == 1 and warmup[0]["max_tokens"] == 8


def test_clean_stops_when_restart_fails(tmp_path, monkeypatch, capsys):
    exp, _, _ = _clean_exp(tmp_path, monkeypatch, "exit 3")
    exp.sweep(["L1"], clean=True)
    assert results.load(exp.results_path) == []
    assert "restart failed, stopping" in capsys.readouterr().err


def test_normal_runs_are_not_cold(tmp_path, monkeypatch):
    exp, _, _ = _clean_exp(tmp_path, monkeypatch, "exit 3")   # never called without --clean
    exp.sweep(["L0"])
    assert results.load(exp.results_path)[0]["cold_start"] is False


def test_queries_bypass_the_embedding_cache(tmp_path):
    from context_bench.embedders import CachedEmbedder, EmbedStats
    from context_bench.retrieval.dense import EmbeddingRetriever
    class Inner:
        name, stats = "fake", EmbedStats()
        def embed(self, texts):
            self.stats.add(embed_texts=len(texts))
            return np.ones((len(texts), 2), dtype=np.float32) / np.sqrt(2)
    cached = CachedEmbedder(Inner(), tmp_path)
    r = EmbeddingRetriever(cached)
    r.index(["a"], ["doc"])
    cached.stats.reset()
    r.search("same query", 1); r.search("same query", 1)
    assert cached.stats.snapshot()["embed_texts"] == 2          # both queries went to the server
    assert len(list(cached.dir.glob("*.npy"))) == 1             # only the document is cached


class ResetClient(ReadyClient):
    def __init__(self, *a, works=True, **k):
        super().__init__(*a, **k)
        self.resets, self.works = 0, works

    def reset_prefix_cache(self):
        self.resets += 1
        return self.works


def _hybrid(tmp_path, monkeypatch, works=True, every="3", free_mb=50_000):
    marker = tmp_path / "restarts.log"
    exp, _, _ = _clean_exp(tmp_path, monkeypatch, f"echo restart >> {marker}")
    client = ResetClient(REPLY, works=works)
    exp._client_factory = lambda m, url: client
    monkeypatch.setenv("CBENCH_RESTART_EVERY", every)
    monkeypatch.setattr("context_bench.runner._free_mb", lambda: free_mb)
    return exp, client, marker


def test_clean_resets_the_cache_between_restarts(tmp_path, monkeypatch):
    exp, client, marker = _hybrid(tmp_path, monkeypatch)
    exp.sweep(["L0", "L1"], clean=True, task_ids=["control-001", "control-002", "control-003"])
    rows = results.load(exp.results_path)
    methods = [r["cold_method"] for r in rows]
    assert all(r["cold_start"] for r in rows) and len(rows) >= 4
    assert methods == ["restart" if i % 3 == 0 else "reset" for i in range(len(rows))]
    restarts = methods.count("restart")
    assert marker.read_text().count("restart") == restarts and client.resets == len(rows) - restarts


def test_clean_restarts_when_reset_is_missing_or_memory_is_low(tmp_path, monkeypatch):
    exp, client, marker = _hybrid(tmp_path, monkeypatch, works=False)
    exp.sweep(["L0"], clean=True)
    assert [r["cold_method"] for r in results.load(exp.results_path)] == ["restart", "restart"]
    (tmp_path / "low").mkdir()
    exp2, client2, marker2 = _hybrid(tmp_path / "low", monkeypatch, free_mb=100)
    exp2.sweep(["L0"], clean=True)
    assert [r["cold_method"] for r in results.load(exp2.results_path)] == ["restart", "restart"]
    assert client2.resets == 0


def test_l7_restarts_on_its_own_interval(tmp_path, monkeypatch):
    exp, client, marker = _hybrid(tmp_path, monkeypatch)
    monkeypatch.setenv("CBENCH_RESTART_EVERY", "100")
    monkeypatch.setenv("CBENCH_RESTART_EVERY_L7", "2")
    monkeypatch.setattr(exp, "restart_server", lambda *a: None)
    monkeypatch.setattr("context_bench.runner._free_mb", lambda: 10**6)
    key = ("m", "u")
    exp._since_restart[key] = 0
    monkeypatch.setattr(exp, "_client", lambda *a: type("C", (), {"reset_prefix_cache": lambda self: True})())
    assert [exp.make_cold("m", "u", "L1") for _ in range(3)] == ["reset"] * 3     # 25-style interval (100)
    assert exp.make_cold("m", "u", "L7") == "restart"                              # 3 + 1 >= 2: restart
    assert [exp.make_cold("m", "u", "L7") for _ in range(2)] == ["reset", "restart"]
