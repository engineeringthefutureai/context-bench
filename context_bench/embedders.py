"""Embedding clients. The embedder is swappable and recorded per run (RunResult.embedder)."""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Protocol

import httpx
import numpy as np

BATCH = 32
# Also cap each request's size: a batch of 32 whole repo-L files (~65k tokens) crashed Ollama on the iGPU
# (2026-11-23), while 8 (~16k tokens) went through. ~48k chars is ~2 full 8k-token windows. One text longer
# than the cap goes alone.
BATCH_CHARS = 24_000


LONG_BATCH = 4          # inputs per request when the window is above Ollama's default 2048 tokens


def _tokens_upper(chars: int) -> int:
    """A safe upper estimate of a text's tokens (code runs ~3 characters per token with EmbeddingGemma)."""
    return chars // 2 + 64


def _pow2(n: int) -> int:
    p = 2048
    while p < n:
        p *= 2
    return p


def _batches(texts: list[str]) -> list[tuple[int, int]]:
    """(start, end) index ranges: at most BATCH texts and about BATCH_CHARS characters each."""
    out, start, size = [], 0, 0
    for i, t in enumerate(texts):
        if i > start and (i - start >= BATCH or size + len(t) > BATCH_CHARS):
            out.append((start, i))
            start, size = i, 0
        size += len(t)
    if start < len(texts):
        out.append((start, len(texts)))
    return out


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


class EmbedStats:
    """Per-thread counters (a sweep runs each run on one worker thread, so they attribute work to
    the run): texts sent to the server, tokens it reports, seconds spent waiting, cache hits."""

    def __init__(self):
        self._local = threading.local()

    def _get(self) -> dict:
        if not hasattr(self._local, "d"):
            self.reset()
        return self._local.d

    def reset(self) -> None:
        self._local.d = {"embed_texts": 0, "embed_tokens": 0, "embed_s": 0.0, "embed_cache_hits": 0}

    def add(self, **values) -> None:
        d = self._get()
        for k, v in values.items():
            d[k] += v

    def snapshot(self) -> dict:
        d = dict(self._get())
        d["embed_s"] = round(d["embed_s"], 3)
        return d


class OpenAICompatEmbedder:
    """Any /v1/embeddings endpoint. Returns L2-normalised float32 rows, so a dot product is cosine."""

    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout: float = 300.0,
                 ollama: bool = False, context_tokens: int | None = None):
        """ollama=True: Ollama's own /api/embed with truncate=false, so an input longer than the model or
        server window is an error instead of being cut silently (the OpenAI-style endpoint truncates and
        can't be told not to)."""
        self.name = model
        self.ollama = ollama
        # Ollama: the window per request; its batch size must match, or input stops at the default 2048
        self.options = {"num_ctx": context_tokens, "num_batch": context_tokens} if context_tokens else {}
        self.stats = EmbedStats()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)

    def embed(self, texts: list[str]) -> np.ndarray:
        if self.ollama and self.options:
            return self._embed_ollama_sized(texts)
        rows: list[list[float]] = []
        for i, j in _batches(texts):
            start = time.monotonic()
            if self.ollama:
                req = {"model": self.name, "input": texts[i:j], "truncate": False}
                if self.options:
                    req["options"] = self.options
                resp = self._http.post("/api/embed", json=req)
                if resp.status_code == 400:
                    raise ValueError(f"embedder refused an input as too long (no truncation): {resp.text[:200]}")
                resp.raise_for_status()
                body = resp.json()
                tokens = int(body.get("prompt_eval_count") or 0)
                vecs = body["embeddings"]
            else:
                resp = self._http.post("/v1/embeddings", json={"model": self.name, "input": texts[i:j]})
                resp.raise_for_status()
                body = resp.json()
                tokens = int((body.get("usage") or {}).get("prompt_tokens") or 0)
                vecs = [d["embedding"] for d in sorted(body["data"], key=lambda d: d["index"])]
            self.stats.add(embed_texts=j - i, embed_s=time.monotonic() - start, embed_tokens=tokens)
            rows += vecs
        v = np.asarray(rows, dtype=np.float32).reshape(len(texts), -1)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)

    def _embed_ollama_sized(self, texts: list[str]) -> np.ndarray:
        """Ollama with a large context window: a request reserves its window for every input in it, so a
        batch of 32 short texts at 8k crashed the iGPU server. Texts are grouped by length and each batch
        asks for the smallest window that holds its longest text (2048 tokens, up to context_tokens), with
        at most LONG_BATCH inputs once the window is above the default."""
        order = sorted(range(len(texts)), key=lambda k: len(texts[k]))
        out: list = [None] * len(texts)
        i = 0
        while i < len(order):
            need = _tokens_upper(len(texts[order[i]]))
            ctx = min(self.options["num_ctx"], max(2048, _pow2(need)))
            limit = BATCH if ctx <= 2048 else LONG_BATCH
            j, size = i, 0
            while j < len(order) and j - i < limit and size + len(texts[order[j]]) <= BATCH_CHARS \
                    and _tokens_upper(len(texts[order[j]])) <= ctx:
                size += len(texts[order[j]])
                j += 1
            j = max(j, i + 1)                    # a single long text always goes, alone if need be
            ctx = min(self.options["num_ctx"], max(ctx, _pow2(_tokens_upper(len(texts[order[j - 1]])))))
            start = time.monotonic()
            batch = [texts[order[k]] for k in range(i, j)]
            resp = self._http.post("/api/embed", json={"model": self.name, "input": batch, "truncate": False,
                                                       "options": {"num_ctx": ctx, "num_batch": ctx}})
            if resp.status_code == 400:
                raise ValueError(f"embedder refused an input as too long (no truncation): {resp.text[:200]}")
            resp.raise_for_status()
            body = resp.json()
            self.stats.add(embed_texts=len(batch), embed_s=time.monotonic() - start,
                           embed_tokens=int(body.get("prompt_eval_count") or 0))
            for k, vec in zip(range(i, j), body["embeddings"]):
                out[order[k]] = vec
            i = j
        v = np.asarray(out, dtype=np.float32).reshape(len(texts), -1)
        return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)


class CachedEmbedder:
    """Wraps an embedder with an on-disk cache keyed by content hash: one .npy per (embedder, text),
    under cache_dir/<embedder name>/. Repo chunks are embedded once per content, whatever the run."""

    def __init__(self, inner: Embedder, cache_dir):
        self.inner, self.name = inner, inner.name
        self.stats = getattr(inner, "stats", None) or EmbedStats()   # shared, so one snapshot has both
        self.dir = Path(cache_dir) / inner.name.replace("/", "_").replace(":", "_")
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()      # sweep workers share one instance

    def _path(self, text: str) -> Path:
        return self.dir / (hashlib.sha256(text.encode()).hexdigest()[:32] + ".npy")

    def _save(self, path: Path, v: np.ndarray) -> None:
        """Write to a temp file and rename, so a reader never sees a half-written vector."""
        fd, tmp = tempfile.mkstemp(dir=self.dir, suffix=".tmp")
        with os.fdopen(fd, "wb") as f:
            np.save(f, v)
        os.replace(tmp, path)

    def store(self, texts: list[str], vectors: np.ndarray) -> None:
        """Save vectors computed elsewhere (the bulk build's workers, as each job finishes). No lock: embed()
        holds it while those workers run, and each write is an atomic rename."""
        for t, v in zip(texts, vectors):
            self._save(self._path(t), v)

    def embed(self, texts: list[str]) -> np.ndarray:
        with self._lock:     # one embedding pass at a time: no duplicate work on a cold cache
            cached = {t: np.load(self._path(t)) for t in set(texts) if self._path(t).is_file()}
            missing = [t for t in dict.fromkeys(texts) if t not in cached]
            self.stats.add(embed_cache_hits=len(set(texts)) - len(missing))
            if missing:
                for t, v in zip(missing, self.inner.embed(missing)):
                    self._save(self._path(t), v)
                    cached[t] = v
        return np.stack([cached[t] for t in texts]) if texts else np.zeros((0, 0), dtype=np.float32)
