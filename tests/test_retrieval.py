"""Retrieval core: code tokenizer, BM25 reference retriever, validate-retrieval."""
from pathlib import Path

from context_bench.retrieval.bm25 import BM25Retriever
from context_bench.retrieval.validate import validate
from context_bench.tasks import load_tasks
from context_bench.tokenize import code_tokens
from context_bench.workspace import load_workspace

ROOT = Path(__file__).resolve().parents[1]


def test_code_tokens_split_snake_and_camel():
    assert code_tokens("normalize_sku") == ["normalize", "sku"]
    assert code_tokens("makeSlug(HTTPError)") == ["make", "slug", "http", "error"]
    assert code_tokens("Order #42, v2") == ["order", "42", "v", "2"]


def test_bm25_ranks_term_matches_first_and_drops_non_matches():
    r = BM25Retriever()
    r.index(["a", "b", "c"], ["def format_weight(grams)", "def slug_for_title(title)",
                             "weight weight of a parcel in grams"])
    hits = r.search("parcel weight", k=5)
    assert [d for d, _ in hits] == ["c", "a"]
    assert hits[0][1] > hits[1][1] > 0
    assert r.search("parcel weight", k=1) == hits[:1]
    assert r.search("nothing matches", k=5) == []


def test_bm25_rare_terms_outweigh_common_ones():
    r = BM25Retriever()
    r.index(["x", "y", "z"], ["the order total", "the order sku", "the order note"])
    assert r.search("the sku", k=1)[0][0] == "y"


def test_validate_on_repo_s_finds_matching_helper_first():
    ws = load_workspace(ROOT / "fixtures/repo_S")
    tasks = [t for t in load_tasks(ROOT / "tasks").values() if t.needed_helpers and not t.repo_sizes]
    rows, table = validate(BM25Retriever, ws, tasks, {"shopkit.utils.text.slug_for_title": "matching"}, set())
    slug = next(r for r in rows if r["helper"].endswith("slug_for_title"))
    assert slug["func_rank"] == 1 and slug["file_rank"] == 1
    assert all(r["task"] != "reuse-002" or "slug_for_title" in r["top3"][0] for r in rows)
    assert "| matching | 1 | 1/1 | 1/1 | 1/1 |" in table
    # the target file is never indexed (levels show it in full)
    target = {t.id: t.target_file for t in tasks}
    assert all(not any(d.startswith(target[r["task"]].removesuffix(".py").replace("/", "."))
                       for d in r["top3"]) for r in rows)


class _FakeEmbedder:
    """Bag of letters a-z: deterministic vectors, no server."""
    name = "fake"

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        import numpy as np
        self.calls += len(texts)
        v = np.array([[t.lower().count(ch) for ch in "abcdefghijklmnopqrstuvwxyz"] for t in texts], dtype=np.float32) + 1e-3
        return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_embedding_retriever_and_cache(tmp_path):
    from context_bench.embedders import CachedEmbedder
    from context_bench.retrieval.dense import EmbeddingRetriever
    inner = _FakeEmbedder()
    cached = CachedEmbedder(inner, tmp_path)
    r = EmbeddingRetriever(cached)
    r.index(["a", "b"], ["zzzz zzz", "aaaa bbb"])
    assert r.search("aab", k=2)[0][0] == "b" and len(r.search("aab", k=1)) == 1
    before = inner.calls
    EmbeddingRetriever(cached).index(["a", "b"], ["zzzz zzz", "aaaa bbb"])   # all from disk
    assert inner.calls == before
