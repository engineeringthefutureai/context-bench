"""Reference dense retriever: brute-force cosine similarity (a few thousand chunks need no
vector database). Also the yardstick for validating any third-party semantic retriever."""
from __future__ import annotations

from typing import Sequence

import numpy as np

from ..embedders import Embedder


class TooLongForEmbedder(ValueError):
    """Texts that must not be split (functions, classes) and don't fit the embedder's window."""


class EmbeddingRetriever:
    name = "embed"

    def __init__(self, embedder: Embedder, query_prefix: str = "", document_prefix: str = "",
                 max_chars: int = 20000, overlap_chars: int = 1500):
        """query_prefix goes in front of every query and document_prefix in front of every indexed text
        (the task formats embedding models are trained with: Qwen3-Embedding instructs queries only,
        EmbeddingGemma prefixes both sides). max_chars / overlap_chars: the sliding window for long texts
        that may be split (index(split_long=True), whole files)."""
        self.embedder, self.query_prefix, self.document_prefix = embedder, query_prefix, document_prefix
        self.max_chars, self.overlap_chars = max_chars, overlap_chars
        self.ids: list[str] = []
        self.vectors = np.zeros((0, 0), dtype=np.float32)
        self.owner: list[int] = []       # vector -> index of the text it belongs to
        self.windowed = 0                # texts embedded in more than one window

    def index(self, ids: Sequence[str], texts: Sequence[str], split_long: bool = False) -> None:
        """Embed every text, never truncated. split_long=True (whole files): a text longer than max_chars
        becomes overlapping line windows, a window the embedder still refuses is halved, and the text
        scores as its best window. split_long=False (functions, classes): each text is embedded whole; any
        the embedder refuses as too long raise TooLongForEmbedder naming them (never cut, never split)."""
        self.ids = list(ids)
        pieces: list[str] = []
        self.owner = []
        for i, t in enumerate(texts):
            ws = windows(t, self.max_chars, self.overlap_chars) if split_long else [t]
            pieces += ws
            self.owner += [i] * len(ws)
        vecs, owner, refused = self._embed(pieces, self.owner, split_long)
        if refused:
            names = sorted({self.ids[k] for k in refused})
            raise TooLongForEmbedder(f"{len(names)} text(s) don't fit the embedder's window and may not be "
                                     f"split: {', '.join(names[:20])}")
        self.owner = owner
        self.windowed = sum(1 for k in set(owner) if owner.count(k) > 1)
        self.vectors = np.asarray(vecs, dtype=np.float32) if vecs else np.zeros((0, 0), dtype=np.float32)

    def _embed(self, pieces: list[str], owner: list[int], split: bool):
        """Embed all pieces; on a too-long refusal, retry one by one: a splittable piece is halved (by
        lines), an unsplittable one is reported. Returns (vectors, owner per vector, refused owners)."""
        if not pieces:
            return [], [], []
        try:
            return list(self.embedder.embed([self.document_prefix + t for t in pieces])), list(owner), []
        except ValueError:
            if len(pieces) == 1 and not split:
                return [], [], [owner[0]]
        vecs, own, refused = [], [], []
        for t, k in zip(pieces, owner):
            try:
                vecs += list(self.embedder.embed([self.document_prefix + t])); own.append(k)
            except ValueError:
                if not split or len(t) < 2:
                    refused.append(k)
                    continue
                lines = t.splitlines(keepends=True)
                mid = max(1, len(lines) // 2) if len(lines) > 1 else 0
                halves = ["".join(lines[:mid]), "".join(lines[mid:])] if mid else [t[:len(t) // 2], t[len(t) // 2:]]
                v, o, r = self._embed(halves, [k, k], split)
                vecs += v; own += o; refused += r
        return vecs, own, refused

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        """Top-k by cosine (vectors are normalised); ties keep index order."""
        if not self.ids:
            return []
        # Queries skip the on-disk cache (a cached query would hide the embedder's real cost); the
        # documents' vectors stand for an index built ahead of time, which is how RAG is deployed.
        embedder = getattr(self.embedder, "inner", self.embedder)
        per_window = self.vectors @ embedder.embed([self.query_prefix + query])[0]
        scores = np.full(len(self.ids), -np.inf, dtype=np.float32)
        np.maximum.at(scores, np.asarray(self.owner), per_window)          # a text = its best window
        order = sorted(range(len(self.ids)), key=lambda i: (-scores[i], i))[:k]
        return [(self.ids[i], float(scores[i])) for i in order]


def windows(text: str, max_chars: int, overlap_chars: int = 1500) -> list[str]:
    """The text itself if it fits, else consecutive line windows of at most max_chars, each starting about
    overlap_chars before the previous one ended (a single longer line is split hard)."""
    if len(text) <= max_chars:
        return [text]
    lines = text.splitlines(keepends=True)
    out, start = [], 0
    while start < len(lines):
        size, end = 0, start
        while end < len(lines) and size + len(lines[end]) <= max_chars:
            size += len(lines[end])
            end += 1
        if end == start:                     # one line longer than a window
            line = lines[start]
            out += [line[i:i + max_chars] for i in range(0, len(line), max_chars)]
            start += 1
            continue
        out.append("".join(lines[start:end]))
        if end >= len(lines):
            break
        back, nxt = 0, end                   # step back over about overlap_chars of lines
        while nxt > start + 1 and back + len(lines[nxt - 1]) <= overlap_chars:
            nxt -= 1
            back += len(lines[nxt])
        start = nxt
    return out
