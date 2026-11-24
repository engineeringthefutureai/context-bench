"""cbench index --embeddings --bulk: build the embedding indexes on both B60s instead of the iGPU.

The sweep's embedder (Ollama on the iGPU) is fine for queries, but embedding every file window and function
of repo L on it takes a long time, and every change of embedder or windowing means doing it again. The bulk
build borrows the two B60s:

1. Through the GPU manager (aries-modeld) it loads the [bulk_index] profiles, one Ollama per card. Loading
   them evicts vLLM, which holds both cards.
2. It indexes the repos exactly as `cbench index --embeddings` does (Experiment.build_indexes: same texts,
   windows, prefixes, no truncation), with an embedder that deals the texts out to both servers.
3. It unloads the bulk profiles and loads again whatever they evicted.

Vectors land in the same on-disk cache as always (keyed by model name and text), so runs reuse them and
embed only their queries, on the iGPU. A short check at the end embeds a sample again on the configured
(iGPU) embedder and reports the lowest cosine between the two, since document vectors now come from one
GPU and query vectors from another.

Why through the manager and not `docker run`: the manager's agent reads every GPU process's memory use from
/proc/<pid>/fdinfo, and pauses those reads only while a container it manages stops. A container stopping
without its knowledge races those reads in the xe driver, the cause of three host lockups (aries-modeld
0.2.5).

Settings: [bulk_index] in config/experiment.toml (profiles, their URLs). The manager's URL and client token
come from ARIES_MANAGER_URL and ARIES_CLIENT_TOKEN (environment or .env), never from the config.
"""
from __future__ import annotations

import queue
import sys
import threading
import time

import httpx
import numpy as np

from .embedders import CachedEmbedder, EmbedStats, OpenAICompatEmbedder
from .llm import env_value

JOB = 16                  # texts per job handed to a server; small, so the two cards finish together
RETRIES = 3               # a job whose server dropped the connection is retried after a pause


class PoolEmbedder:
    """Deals texts out to several embedders of the same model, one worker thread each, longest texts first
    (so neither card is left with all the long windows at the end). Returns rows in input order.

    on_done(texts, vectors), if set, is called after every finished job: the bulk build saves vectors to
    the cache as they arrive, so an error late in a long pass loses only the jobs in flight.
    A job a server refuses as too long is retried text by text; the refused texts make embed() raise
    ValueError at the end (as a single embedder does), after everything else is done and saved."""

    def __init__(self, embedders: list, labels: list[str] | None = None):
        self.embedders = embedders
        self.name = embedders[0].name
        self.labels = labels or [str(i) for i in range(len(embedders))]
        self.stats = EmbedStats()
        self.per_server = {lab: 0 for lab in self.labels}
        self.on_done = None

    def embed(self, texts: list[str]) -> np.ndarray:
        order = sorted(range(len(texts)), key=lambda k: -len(texts[k]))
        jobs: queue.Queue = queue.Queue()
        for i in range(0, len(order), JOB):
            jobs.put(order[i:i + JOB])
        out: list = [None] * len(texts)
        refused: list[str] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def run(emb, label):
            while not errors:
                try:
                    idx = jobs.get_nowait()
                except queue.Empty:
                    return
                batch = [texts[k] for k in idx]
                try:
                    vecs = self._embed_job(emb, batch)
                except ValueError:
                    vecs = []
                    for t in batch:                  # find the refused text(s), keep the rest
                        try:
                            vecs.append(self._embed_job(emb, [t])[0])
                        except ValueError as e:
                            vecs.append(None)
                            with lock:
                                refused.append(str(e)[:200])
                except BaseException as e:      # noqa: BLE001  (reported after the other workers stop)
                    with lock:
                        errors.append(e)
                    return
                done = [(k, v) for k, v in zip(idx, vecs) if v is not None]
                for k, v in done:
                    out[k] = v
                with lock:
                    self.per_server[label] += len(done)
                if self.on_done and done:
                    self.on_done([texts[k] for k, _ in done], np.stack([v for _, v in done]))

        threads = [threading.Thread(target=run, args=(e, lab), daemon=True)
                   for e, lab in zip(self.embedders, self.labels)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if errors:
            raise errors[0]
        if refused:
            raise ValueError(f"embedder refused {len(refused)} input(s) as too long (no truncation): {refused[0]}")
        self.stats.add(embed_texts=len(texts))
        return np.stack(out) if texts else np.zeros((0, 0), dtype=np.float32)

    @staticmethod
    def _embed_job(emb, batch: list[str]) -> np.ndarray:
        for attempt in range(RETRIES + 1):
            try:
                return emb.embed(batch)
            except httpx.TransportError:
                if attempt == RETRIES:
                    raise
                time.sleep(10 * 3 ** attempt)    # 10, 30, 90 s: Ollama restarts its runner on the next request
        raise AssertionError("unreachable")


class Manager:
    """The few aries-modeld calls the bulk build needs (POST /load, /unload; GET /state)."""

    def __init__(self, url: str, token: str, timeout: float = 30.0):
        self._http = httpx.Client(base_url=url.rstrip("/"), timeout=timeout,
                                  headers={"Authorization": f"Bearer {token}"})

    def states(self) -> dict[str, str]:
        r = self._http.get("/state")
        r.raise_for_status()
        return {p["name"]: p["state"] for p in r.json()["profiles"]}

    def _wait(self, profile: str, want: set[str], timeout: float) -> None:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            st = self.states().get(profile)
            if st in want:
                return
            if st == "FAILED":
                raise RuntimeError(f"manager: {profile} FAILED (see its log in the manager UI)")
            time.sleep(5)
        raise TimeoutError(f"manager: {profile} not {'/'.join(sorted(want))} after {timeout:.0f}s")

    def load(self, profile: str, evict: bool, timeout: float = 900) -> None:
        r = self._http.post("/load", json={"profile": profile, "client": "cbench-index", "wait": False,
                                            "evict": evict})
        if r.status_code >= 400:
            raise RuntimeError(f"manager: load {profile}: HTTP {r.status_code} {r.text[:300]}")
        self._wait(profile, {"READY"}, timeout)

    def unload(self, profile: str, timeout: float = 300) -> None:
        r = self._http.post("/unload", json={"profile": profile, "client": "cbench-index", "wait": False})
        if r.status_code >= 400:
            raise RuntimeError(f"manager: unload {profile}: HTTP {r.status_code} {r.text[:300]}")
        self._wait(profile, {"STOPPED"}, timeout)


def _log(msg: str) -> None:
    print("  bulk " + msg, file=sys.stderr, flush=True)


def _wait_ollama(url: str, model: str, timeout: float = 300) -> None:
    """Until the server answers and the model is pulled (it shares the iGPU server's model store)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            r = httpx.get(url.rstrip("/") + "/api/tags", timeout=10)
            if r.status_code == 200:
                if model in {m["name"] for m in r.json().get("models", [])}:
                    return
                raise RuntimeError(f"{url}: model {model} is not in its store (ollama pull it once)")
        except httpx.TransportError:
            pass
        time.sleep(3)
    raise TimeoutError(f"{url}: Ollama not answering after {timeout:.0f}s")


def agreement(cache: CachedEmbedder, reference, texts: list[str]) -> float:
    """Lowest cosine between the cached (bulk-built) vectors of texts and the same texts embedded again by
    the reference embedder (uncached)."""
    a = cache.embed(texts)
    b = reference.embed(texts)
    return float(np.min(np.sum(a * b, axis=1)))


def bulk_build(exp, repo_sizes: list[str], sample: int = 12) -> list[str]:
    """See the module docstring. Returns build_indexes' report lines plus the bulk ones."""
    cfg = exp.config.get("bulk_index") or {}
    ecfg = exp.config["embedder"]
    url, token = env_value("ARIES_MANAGER_URL"), env_value("ARIES_CLIENT_TOKEN")
    if not url or not token:
        raise SystemExit("--bulk needs ARIES_MANAGER_URL and ARIES_CLIENT_TOKEN (environment or .env)")
    profiles, urls = list(cfg.get("profiles", [])), list(cfg.get("base_urls", []))
    if not profiles or len(profiles) != len(urls):
        raise SystemExit("[bulk_index] needs profiles and base_urls of the same length")
    mgr = Manager(url, token)

    before = mgr.states()
    missing = [p for p in profiles if p not in before]
    if missing:
        raise SystemExit(f"manager has no profile {', '.join(missing)} (add it to its profiles.yaml)")
    was_ready = {p for p, s in before.items() if s == "READY" and p not in profiles}
    lines: list[str] = []
    start = time.monotonic()
    try:
        for p in profiles:
            _log(f"loading {p} (evicts what holds its card)")
            mgr.load(p, evict=True)
        for u in urls:
            _wait_ollama(u, ecfg["model"])
        _log(f"ready after {time.monotonic() - start:.0f}s: {', '.join(profiles)}")

        pool = PoolEmbedder([OpenAICompatEmbedder(u, ecfg["model"], ollama=True,
                                                  context_tokens=ecfg.get("context_tokens"))
                             for u in urls], labels=profiles)
        cache = CachedEmbedder(pool, exp._path(exp.config["paths"]["cache"]) / "embeddings")
        pool.on_done = cache.store
        configured, exp._embedder = exp._embedder, cache
        try:
            lines += exp.build_indexes(repo_sizes)
        finally:
            exp._embedder = configured
        lines.append("per server: " + ", ".join(f"{k} {v} texts" for k, v in pool.per_server.items()))

        if sample:
            texts = _sample_texts(exp, repo_sizes, sample)
            low = agreement(cache, exp.embedder().inner, texts)
            lines.append(f"agreement with the configured embedder on {len(texts)} texts: lowest cosine {low:.5f}")
            if low < 0.999:
                lines.append("WARNING: bulk vectors differ from the configured embedder's; don't mix them")
    finally:
        now = mgr.states()
        for p in profiles:
            if now.get(p) not in ("STOPPED", None):
                _log(f"unloading {p}")
                mgr.unload(p)
        back = [p for p in sorted(was_ready) if mgr.states().get(p) != "READY"]
        for p in back:
            _log(f"loading {p} again")
            mgr.load(p, evict=False)
        lines.append(f"bulk build {time.monotonic() - start:.0f}s in all"
                     + (f"; reloaded {', '.join(back)}" if back else ""))
    for line in lines[-3:]:
        _log(line)
    return lines


def _sample_texts(exp, repo_sizes: list[str], n: int) -> list[str]:
    """A few document texts as the index holds them (prefixed): short and long functions, file windows."""
    from .retrieval.base import chunk_text, file_text
    from .retrieval.dense import windows
    from .workspace import load_workspace
    ecfg = exp.config["embedder"]
    prefix = ecfg.get("document_prefix", "")
    ws = load_workspace(exp._path(exp.config["repos"][repo_sizes[-1]]))
    chunks = sorted((chunk_text(c) for c in ws.chunks), key=len)
    files = sorted((file_text(p, ws.files[p]) for p in ws.files), key=len)
    picks = chunks[:: max(1, len(chunks) // (n // 2))][: n // 2]
    for f in files[-(n - len(picks)):]:
        picks.append(windows(f, int(ecfg.get("max_chars", 10000)), int(ecfg.get("overlap_chars", 1000)))[0])
    return [prefix + t for t in picks]
