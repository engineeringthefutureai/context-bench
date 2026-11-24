"""The bulk index builder's pool: order, both servers used, refusals, vectors saved as jobs finish."""
import threading
import time

import numpy as np
import pytest

from context_bench.bulk_index import PoolEmbedder
from context_bench.embedders import CachedEmbedder


class Fake:
    """Vector = [len(text), 1]; refuses texts containing "TOOLONG"; counts what it embedded."""

    def __init__(self, name="m"):
        self.name, self.seen = name, []
        self._lock = threading.Lock()

    def embed(self, texts):
        if any("TOOLONG" in t for t in texts):
            raise ValueError("embedder refused an input as too long")
        time.sleep(0.002)                    # a real server takes time: lets both workers get jobs
        with self._lock:
            self.seen += texts
        v = np.array([[len(t), 1.0] for t in texts], dtype=np.float32)
        return v / np.linalg.norm(v, axis=1, keepdims=True)


def _expected(texts):
    v = np.array([[len(t), 1.0] for t in texts], dtype=np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_rows_come_back_in_input_order_and_both_servers_work():
    a, b = Fake(), Fake()
    pool = PoolEmbedder([a, b], labels=["b60-1", "b60-2"])
    texts = ["x" * n for n in range(1, 200)]
    np.testing.assert_allclose(pool.embed(texts), _expected(texts), rtol=1e-6)
    assert a.seen and b.seen and sorted(a.seen + b.seen) == sorted(texts)
    assert sum(pool.per_server.values()) == len(texts)


def test_refused_texts_raise_after_the_rest_is_saved(tmp_path):
    pool = PoolEmbedder([Fake(), Fake()])
    cache = CachedEmbedder(pool, tmp_path)
    pool.on_done = cache.store
    texts = ["ok %d" % i for i in range(40)] + ["TOOLONG"]
    with pytest.raises(ValueError, match="refused 1 input"):
        cache.embed(texts)
    # everything but the refused text is in the cache: a retry embeds only that one
    assert all(cache._path(t).is_file() for t in texts[:-1]) and not cache._path("TOOLONG").is_file()


def test_pool_shares_the_cache_with_the_configured_embedder(tmp_path):
    """Same model name -> same cache directory, so runs on the iGPU reuse the bulk-built vectors."""
    pool_cache = CachedEmbedder(PoolEmbedder([Fake("gemma"), Fake("gemma")]), tmp_path)
    pool_cache.embed(["def f(): pass"])
    single = Fake("gemma")
    CachedEmbedder(single, tmp_path).embed(["def f(): pass"])
    assert single.seen == []
