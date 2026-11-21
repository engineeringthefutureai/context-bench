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

    def __init__(self, base_url: str, model: str, api_key: str | None = None, timeout: float = 300.0):
        self.name = model
        self.stats = EmbedStats()
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(base_url=base_url, headers=headers, timeout=timeout)

    def embed(self, texts: list[str]) -> np.ndarray:
        rows: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            start = time.monotonic()
            resp = self._http.post("/v1/embeddings", json={"model": self.name, "input": texts[i:i + BATCH]})
            resp.raise_for_status()
            body = resp.json()
            self.stats.add(embed_texts=len(texts[i:i + BATCH]), embed_s=time.monotonic() - start,
                           embed_tokens=int((body.get("usage") or {}).get("prompt_tokens") or 0))
            data = sorted(body["data"], key=lambda d: d["index"])
            rows += [d["embedding"] for d in data]
        v = np.asarray(rows, dtype=np.float32).reshape(len(texts), -1)
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
