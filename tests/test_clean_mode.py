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
    exp.sweep(["L0", "L4"], clean=True)           # L4 isn't implemented: no restart spent on it
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
