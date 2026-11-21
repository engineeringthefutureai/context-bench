"""Orchestration. One run:

  1. copy the repo into a fresh sandbox
  2. build the context with LEVELS[level](task, workspace, **variant options)
  3. render the fixed prompt; count tokens; if tokens + max_output_tokens > context_window
     -> ctx_fits=false, label no_fit, stop
  4. call the model (L7: run the tool loop, which applies the same fit check before every turn);
     log usage to stderr as it goes (tokens in/out,
     peak context vs window, latency, tool calls) so it's visible during testing
  5. parse <file> blocks, apply them, save the patch
  6. run hidden tests: correctness tests -> label (ok / broken); test_reuse.py -> reuse_passed
  7. write the run bundle (context_bench/bundle.py) and return a RunResult
     (helper_in_context, recall, label, reuse_passed, cost)

Grading is a separate, later stage: `cbench grade` (context_bench/grade.py).
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import subprocess
import tempfile
import time
from datetime import datetime, timezone
import threading
import traceback
import tomllib
from dataclasses import asdict
from pathlib import Path

import httpx

from . import bundle, results
from .apply import (UnsafePath, apply_files, format_status, make_sandbox, parse_files,
                    parse_files_lenient, unified_patch)
from .evaluate.hidden_tests import run_hidden_tests
from .levels import LEVELS
from .levels.l7_agentic import RepoTools, cacheable_prefix, run_agent, tool_schemas, turn_prompts
from .llm import ChatResult, OpenAICompatClient, env_value
from .metrics import dead_modules, legacy_ctx_share, legacy_used, usage_visible
from .prompt import PROMPT_VERSION, render
from .embedders import CachedEmbedder, OpenAICompatEmbedder
from .retrieval.bm25 import BM25Retriever
from .retrieval.dense import EmbeddingRetriever
from .results import config_label
from .tasks import load_tasks
from .types import ContextBlock, RunConfig, RunResult, Task
from .workspace import Workspace, load_workspace


class Experiment:
    def __init__(self, config_path: Path, client_factory=None):
        """Paths in the config are relative to the project root (the config file's grandparent).
        client_factory(model_cfg, base_url) -> client; defaults to OpenAICompatClient (tests pass a fake).
        A model may list several identical servers as `base_urls`; the sweep runs one job per server."""
        self.config = tomllib.loads(config_path.read_text())
        self.root = config_path.resolve().parent.parent
        paths = self.config["paths"]
        self.tasks = load_tasks(self._path(paths["tasks"]))
        self.results_path = self._path(paths["results"])
        self.runs_dir = self._path(paths["runs"])
        self.shared_dir = self._path(paths["tasks"]) / "_shared"
        reg_path = self._path(paths["helpers"]) if paths.get("helpers") else None
        registry = tomllib.loads(reg_path.read_text()) if reg_path and reg_path.is_file() else {}
        self.legacy = registry.get("legacy", {}).get("paths", [])   # dead code: harness-only
        self.default_model = self.config["sweep"]["models"][0]
        self._client_factory = client_factory or (
            lambda m, url: OpenAICompatClient(url, m["model"]))
        self._clients: dict[tuple[str, str], object] = {}
        self._sampling: dict[str, dict] = {}
        self._backend: dict[tuple[str, str], str] = {}
        self._repo_hash: dict[str, str] = {}
        self._embedder = None
        self._client_lock = threading.Lock()

    def embedder(self):
        """The configured [embedder], with an on-disk cache under [paths].cache/embeddings."""
        with self._client_lock:
            return self._make_embedder()

    def _make_embedder(self):
        if self._embedder is None:
            cfg = self.config["embedder"]
            self._embedder = CachedEmbedder(OpenAICompatEmbedder(cfg["base_url"], cfg["model"]),
                                            self._path(self.config["paths"]["cache"]) / "embeddings")
        return self._embedder

    def retrievers(self) -> dict:
        """Retriever name -> factory. "embed" uses the configured embedder and query_prefix."""
        prefix = self.config.get("embedder", {}).get("query_prefix", "")
        return {"bm25": BM25Retriever,
                "embed": lambda: EmbeddingRetriever(self.embedder(), query_prefix=prefix)}

    def _path(self, p: str) -> Path:
        return Path(p) if Path(p).is_absolute() else self.root / p

    def repo_hash(self, repo_size: str) -> str:
        """sha256 over the repo's files (sorted paths + contents, caches skipped), first 12 hex chars.
        Changes whenever the fixture changes, so stale results can be told apart and rerun."""
        if repo_size not in self._repo_hash:
            root = self._path(self.config["repos"][repo_size])
            h = hashlib.sha256()
            for f in sorted(p for p in root.rglob("*") if p.is_file()
                            and not {"__pycache__", ".pytest_cache"} & set(p.parts)):
                h.update(str(f.relative_to(root)).encode() + b"\0" + f.read_bytes() + b"\0")
            self._repo_hash[repo_size] = h.hexdigest()[:12]
        return self._repo_hash[repo_size]

    def base_urls(self, model_key: str) -> list[str]:
        m = self.config["models"][model_key]
        return m.get("base_urls") or [m["base_url"]]

    def _client(self, model_key: str, url: str | None = None):
        url = url or self.base_urls(model_key)[0]
        with self._client_lock:     # sweep workers may share a URL: publish the client last
            if (model_key, url) not in self._clients:
                client = self._client_factory(self.config["models"][model_key], url)
                if model_key not in self._sampling:
                    self._sampling[model_key] = client.server_sampling()
                self._backend[model_key, url] = client.backend() if hasattr(client, "backend") else "unknown"
                self._clients[model_key, url] = client
            return self._clients[model_key, url]

    def restart_server(self, model_key: str, url: str) -> None:
        """Clean mode: run CBENCH_RESTART_CMD (environment or .env; a shell command that returns once
        the old server has stopped), wait up to CBENCH_READY_TIMEOUT seconds (default 1200) for the
        server to answer as ready, then send one tiny warm-up request that shares nothing with our
        prompts (too short to leave a reusable cache block). Raises if any step fails."""
        cmd = env_value("CBENCH_RESTART_CMD")
        if not cmd:
            raise RuntimeError("clean mode needs CBENCH_RESTART_CMD (environment or .env)")
        timeout = float(env_value("CBENCH_READY_TIMEOUT") or 1200)
        subprocess.run(cmd, shell=True, check=True, timeout=timeout,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        with self._client_lock:                  # pooled connections died with the old server
            self._clients.pop((model_key, url), None)
        client = self._client_factory(self.config["models"][model_key], url)
        deadline = time.monotonic() + timeout
        while not client.ready():
            if time.monotonic() > deadline:
                raise RuntimeError(f"model server not ready {timeout:.0f}s after restart")
            time.sleep(2)
        client.chat([{"role": "user", "content": "Reply with OK."}], temperature=None, seed=1,
                    max_tokens=8, thinking=False)

    def run_one(self, rc: RunConfig, base_url: str | None = None, concurrency: int = 1,
                cold_start: bool = False) -> RunResult:
        """One run; base_url picks one of the model's servers (default: the first); concurrency is
        how many runs share that server (recorded, so latency can be read in context).
        cold_start=True restarts the server (restart_server) right before the run's first model call,
        so no prefix cache survives from earlier runs; context building happens first, so a run whose
        level isn't implemented never costs a restart."""
        task = self.tasks[rc.task_id]
        model_cfg = self.config["models"][rc.model]
        gen = self.config["generation"]
        max_out, window = gen["max_output_tokens"], model_cfg["context_window"]
        client = self._client(rc.model, base_url)
        url = base_url or self.base_urls(rc.model)[0]
        restarted = False

        def model_client():
            """The client for model calls; in clean mode the first call restarts the server."""
            nonlocal client, restarted
            if cold_start and not restarted:
                self.restart_server(rc.model, url)
                client, restarted = self._client(rc.model, url), True
            return client
        perf = dict(thinking=rc.thinking, map_docs=rc.map_docs, prompt_version=rc.prompt_version, repo_hash=self.repo_hash(rc.repo_size), backend=self._backend[rc.model, base_url or self.base_urls(rc.model)[0]],
                    concurrency=concurrency)
        run_dir = self.runs_dir / rc.run_id

        with tempfile.TemporaryDirectory(prefix="cbench-") as tmp:
            sandbox = make_sandbox(self._path(self.config["repos"][rc.repo_size]), Path(tmp) / "repo")
            ws = load_workspace(sandbox)
            ws.retrievers = {"embed": self.retrievers()["embed"]}
            uses_embedder = rc.retriever == "embed" or rc.agent_variant == "hybrid"
            if uses_embedder:
                self.embedder().stats.reset()
            options = _level_options(rc)
            if rc.query_mode == "model_queries":
                def planner(t: Task, l1: ContextBlock, instruction: str):
                    msgs = render(t, l1, rc.prompt_arm, rc.prompt_version)
                    msgs[-1] = {**msgs[-1], "content": msgs[-1]["content"] + "\n\n" + instruction}
                    plan = model_client().chat(msgs, temperature=rc.temperature, seed=rc.seed, max_tokens=max_out,
                                       **({} if rc.thinking else {"thinking": False}))
                    return plan.content, plan.tokens_in, plan.tokens_out, plan.latency_s
                options["planner"] = planner
            perf["started_at"] = _now()
            ctx = LEVELS[rc.level](task, ws, **options)
            if uses_embedder:
                perf.update(embedder=self.embedder().name, **self.embedder().stats.snapshot())
            messages = render(task, ctx, rc.prompt_arm, rc.prompt_version)
            prompt_tokens = client.count_prompt_tokens(messages)
            base = dict(rc=rc, task=task, model_cfg=model_cfg, window=window, max_out=max_out, ctx=ctx,
                        sampling=self._sampling[rc.model], ws=ws)

            if prompt_tokens + max_out > window:
                result = _result(**base, label="no_fit", ctx_fits=False, tests=(0, 0, None),
                                 tokens=(prompt_tokens, 0, prompt_tokens), latency=0.0, patch_path=None,
                                 error=f"prompt {prompt_tokens} + max_output {max_out} > window {window}")
                _set(result, perf)
                bundle.write_bundle(run_dir, patch=None, messages=messages, response="", reasoning=None,
                                    transcript=None, tests_output="", result=asdict(result))
                _log(result)
                return result

            agent = None
            if rc.level == "L7":
                tools = RepoTools(sandbox, make_retriever=(ws.retrievers["embed"]
                                                           if rc.agent_variant == "hybrid" else None))
                agent = run_agent(model_client(), messages, tools, agent_variant=rc.agent_variant,
                                  temperature=rc.temperature, seed=rc.seed, max_tokens=max_out,
                                  context_window=window, thinking=rc.thinking)
                chat = ChatResult(content=agent.content, reasoning=agent.reasoning,
                                  tokens_in=agent.tokens_in, tokens_out=agent.tokens_out,
                                  latency_s=agent.latency_s, finish_reason=agent.finish_reason)
                # helper_in_context for L7: the definition appeared in any tool output
                ctx.text += "\n\n" + "\n\n".join(tools.transcript)
                perf.update(tool_calls=agent.tool_calls, tokens_cached=agent.tokens_cached,
                            tokens_cacheable=agent.tokens_cacheable)
            else:
                chat = model_client().chat(messages, temperature=rc.temperature, seed=rc.seed, max_tokens=max_out,
                                           **({} if rc.thinking else {"thinking": False}))
            perf.update(finished_at=_now(), prefill_s=_round(chat.prefill_s), decode_s=_round(chat.decode_s),
                        cold_start=restarted)
            if uses_embedder:
                perf.update(self.embedder().stats.snapshot())
            if not agent:
                perf["tokens_cached"] = chat.cached_tokens
            if agent and agent.no_fit:
                result = _result(**base, label="no_fit", ctx_fits=False, tests=(0, 0, None),
                                 tokens=(agent.tokens_in, agent.tokens_out, agent.peak_ctx_tokens),
                                 latency=agent.latency_s, patch_path=None,
                                 error=f"a turn would exceed the window ({agent.peak_ctx_tokens} + {max_out} > {window})")
                _set(result, perf)
                bundle.write_bundle(run_dir, patch=None, messages=messages, response="", reasoning=chat.reasoning,
                                    transcript=agent.transcript, tests_output="", result=asdict(result))
                _log(result)
                return result

            files, error = parse_files(chat.content), None
            if chat.finish_reason == "length":
                error = f"output hit max_output_tokens ({max_out})"
            elif not files:
                error = "no <file> blocks in the response"
            patch = None
            try:
                before = apply_files(sandbox, files)
                patch = unified_patch(before, files) or None
            except UnsafePath as e:
                error = f"unsafe path: {e}"
            passed, total, reuse, tests_out = run_hidden_tests(sandbox, task.hidden_tests, self.shared_dir)
        perf.update(self._protocol(task, rc.repo_size, chat.content, files))
        if chat.tool_calls and not files:       # the server parsed pretend tool calls out of the text
            perf["format_status"] = "tool_request"

        label = "ok" if total > 0 and passed == total else "broken"
        if agent and agent.tool_errors and not files:
            label, error = "tool_error", f"{agent.tool_errors} tool errors and no <file> blocks"
        result = _result(**base, label=label, ctx_fits=True, tests=(passed, total, reuse),
                         tokens=(chat.tokens_in + ctx.extra_tokens_in, chat.tokens_out + ctx.extra_tokens_out,
                                 agent.peak_ctx_tokens if agent else max(prompt_tokens, chat.tokens_in)),
                         latency=chat.latency_s + ctx.extra_latency_s,
                         patch_path=str((run_dir / "patch.diff").relative_to(self.root)) if patch else None,
                         error="; ".join(e for e in (error, ctx.note) if e) or None)
        _set(result, perf)
        _set(result, self._seen_metrics(task, ctx.text, ws, patch))
        # L7: the whole tool conversation. Otherwise side exchanges (e.g. the L5 planning call), plus
        # the raw message when the text alone doesn't explain the reply (the server lifted pretend
        # tool calls out of an otherwise empty content).
        transcript = agent.transcript if agent else list(ctx.transcript)
        if not agent and (chat.tool_calls or not chat.content.strip()):
            transcript.append({"finish_reason": chat.finish_reason, "message": chat.message})
        bundle.write_bundle(run_dir, patch=patch, messages=messages, response=chat.content,
                            reasoning=chat.reasoning, transcript=transcript or None,
                            tests_output=tests_out, result=asdict(result))
        _log(result)
        return result

    def _seen_metrics(self, task: Task, seen: str, ws: Workspace, patch: str | None) -> dict:
        """Metrics from what the run saw (context + tool output) and what it wrote (metrics.py)."""
        return {"helper_usage_in_context": usage_visible(task.needed_helpers, seen),
                "legacy_ctx_share": legacy_ctx_share(seen, self.legacy),
                "legacy_used": (legacy_used(patch, dead_modules(self.legacy, list(ws.files)))
                                if patch else None)}

    def _protocol(self, task: Task, repo_size: str, response: str, files: dict[str, str]) -> dict:
        """format_status, and when the strict parse found nothing but there is code, the hidden-test
        result of the leniently extracted code (a separate sandbox; the label is not affected)."""
        out = {"format_status": format_status(response, files), "lenient_passed": None,
               "lenient_total": None, "lenient_reuse": None}
        if out["format_status"] == "ok" or not (lenient := parse_files_lenient(response, task.target_file)):
            return out
        with tempfile.TemporaryDirectory(prefix="cbench-lenient-") as tmp:
            box = make_sandbox(self._path(self.config["repos"][repo_size]), Path(tmp) / "repo")
            try:
                apply_files(box, lenient)
            except UnsafePath:
                return out
            p, n, reuse, _ = run_hidden_tests(box, task.hidden_tests, self.shared_dir)
        out.update(lenient_passed=p, lenient_total=n, lenient_reuse=reuse)
        return out

    def rescore(self) -> int:
        """Recompute the output-protocol fields of recorded runs from their saved responses, and the
        helper visibility fields from their saved prompts and tool outputs (no model calls): after
        the parser, classifier or visibility rules change, or for runs recorded before they existed.
        Skips runs whose repo has changed since (repo_hash). Rewrites results.jsonl; returns the count."""
        rows, n = results.load(self.results_path), 0
        workspaces = {size: load_workspace(self._path(path)) for size, path in self.config["repos"].items()}
        for r in rows:
            response = self.runs_dir / r["run_id"] / "response.txt"
            task = self.tasks.get(r["task_id"])
            if r["label"] == "no_fit" or task is None or not response.is_file() \
                    or r.get("repo_hash") not in (None, self.repo_hash(r["repo_size"])):
                continue
            text = response.read_text()
            files = parse_files(text)
            r.update(self._protocol(task, r["repo_size"], text, files))
            if not files and _had_tool_calls(self.runs_dir / r["run_id"] / "transcript.jsonl", r["level"]):
                r["format_status"] = "tool_request"     # as run_one decided from the raw message
            if (seen := _seen_text(self.runs_dir / r["run_id"])) is not None:
                ctx, ws = ContextBlock(text=seen), workspaces[r["repo_size"]]
                r["helper_in_context"] = _helper_visible(task, ctx)
                r["helper_body_in_context"] = _helper_body_visible(task, ctx, ws)
                patch_file = self.runs_dir / r["run_id"] / "patch.diff"
                r.update(self._seen_metrics(task, seen, ws,
                                            patch_file.read_text() if patch_file.is_file() else None))
            n += 1
        results.rewrite(self.results_path, rows)
        return n

    def backfill_cacheable(self, model_key: str) -> int:
        """tokens_cacheable (and the true per-turn peak) for L7 runs recorded before they existed or
        with peak = total input (a bug fixed 2026-11-21): rebuild every turn's request from the bundle (prompt.json + transcript.jsonl) and count its prompt tokens on `model_key`'s server
        (tokenizer only, no generation). Only rows of that model. Returns the count."""
        model = self.config["models"][model_key]["model"]
        client = self._client(model_key)
        rows, n = results.load(self.results_path), 0
        schema_cache: dict[str, int] = {}
        for r in rows:
            run = self.runs_dir / r["run_id"]
            if r["level"] != "L7" or r["model"] != model or not (run / "transcript.jsonl").is_file() \
                    or ("tokens_cacheable" in r and r["peak_ctx_tokens"] < r["tokens_in"]):
                continue
            messages = json.loads((run / "prompt.json").read_text())
            transcript = [json.loads(line) for line in (run / "transcript.jsonl").read_text().splitlines()]
            if r["agent_variant"] not in schema_cache:
                schema_cache[r["agent_variant"]] = client.count_tokens(json.dumps(tool_schemas(r["agent_variant"])))
            total, prev, peak = 0, (0, None), 0
            for msgs, offer in turn_prompts(messages, transcript):
                tokens = client.count_prompt_tokens(msgs) + (schema_cache[r["agent_variant"]] if offer else 0)
                total += cacheable_prefix(prev[0], prev[1], tokens, offer)
                prev, peak = (tokens, offer), max(peak, tokens)
            r["tokens_cacheable"] = total
            r["peak_ctx_tokens"] = peak
            n += 1
        results.rewrite(self.results_path, rows)
        return n

    def configs(self, levels: list[str] | None = None, models: list[str] | None = None,
                repo_sizes: list[str] | None = None, thinking: bool | None = None,
                task_ids: list[str] | None = None) -> list[RunConfig]:
        """Expand [[config]] entries x models x repo sizes x seeds x tasks into RunConfigs.
        A config's optional `categories` and `tiers` keep only matching tasks (e.g. L0: tier 1).
        `levels` / `repo_sizes` keep only those (cbench run --level / --repo-size); `models` replaces the
        sweep's models; `task_ids` keeps only those tasks (cbench run --task). Thinking comes from [generation].thinking (default on), a config's own
        `thinking` key, or `thinking` here, which overrides both (cbench run --thinking/--no-thinking)."""
        sweep = self.config["sweep"]
        out = []
        for c in self.config.get("config", []):
            if levels and c["level"] not in levels:
                continue
            tasks = [t for t in sorted(self.tasks.values(), key=lambda t: (t.tier, t.id))
                     if (not task_ids or t.id in task_ids)
                     and ("categories" not in c or t.category in c["categories"])
                     and ("tiers" not in c or t.tier in c["tiers"])]
            for model in models or c.get("models", sweep["models"]):
                sizes = c.get("repo_sizes", sweep["default_repo_sizes"])
                for size in sweep["default_repo_sizes"]:
                    if repo_sizes and size not in repo_sizes:
                        continue
                    fitting = [t for t in tasks if _runs_on(t, size, sizes, sweep["default_repo_sizes"])]
                    for seed in sweep["seeds"]:
                        out += [RunConfig(task_id=t.id, level=c["level"], model=model, seed=seed,
                                          repo_size=size, retriever=c.get("retriever", "none"),
                                          query_mode=c.get("query_mode", "none"),
                                          agent_variant=c.get("agent_variant", "none"),
                                          prompt_arm=c.get("prompt_arm", "base"),
                                          temperature=self.config["generation"].get("temperature"),
                                          thinking=(thinking if thinking is not None else
                                                    c.get("thinking", self.config["generation"].get("thinking", True))),
                                          map_docs=c.get("map_docs", False),
                                          prompt_version=PROMPT_VERSION)
                                for t in fitting]
        return out

    def sweep(self, levels: list[str] | None = None, dry_run: bool = False,
              models: list[str] | None = None, repo_sizes: list[str] | None = None,
              thinking: bool | None = None, task_ids: list[str] | None = None, rerun: bool = False,
              clean: bool = False) -> None:
        """Run every config not already in results.jsonl (or whose repo has changed since: a recorded
        repo_hash that differs from the fixture now); rerun=True runs them all again (rows are appended;
        the report reads the latest per run_id). Failures aren't written, so they retry. Configs that
        collapse to the same run_id (e.g. a thinking arm under --no-thinking) run once.
        A level or variant that isn't implemented yet is skipped (reported once); a server error (timeout,
        connection, HTTP status) skips that run, so the next `cbench run` retries it.
        clean=True: serial (one run at a time, each model's first server only) and the server is
        restarted before every run (restart_server), so every run starts from a cold prefix cache. A
        failed restart stops the sweep."""
        recorded = {r["run_id"]: r.get("repo_hash") for r in results.load(self.results_path)}
        unique = list({rc.run_id: rc for rc in self.configs(levels, models, repo_sizes, thinking, task_ids)}.values())
        todo = [rc for rc in unique
                if rerun or rc.run_id not in recorded
                or recorded[rc.run_id] not in (None, self.repo_hash(rc.repo_size))]
        done = recorded.keys()
        print(f"{len(todo)} runs to do ({len(done)} already in {self.results_path.name})", file=sys.stderr)
        if dry_run:
            for rc in todo:
                print(f"  {rc.task_id} {config_label(asdict(rc))}", file=sys.stderr)
            return
        unimplemented: set[str] = set()
        failed: list[str] = []
        lock = threading.Lock()
        counter = iter(range(1, len(todo) + 1))

        def worker(url: str, queue: list[RunConfig]) -> None:
            while True:
                with lock:
                    if not queue:
                        return
                    rc = queue.pop(0)
                    if _variant(rc) in unimplemented:
                        continue
                    i = next(counter)
                tag = f"[{rc.task_id} {config_label(asdict(rc))}]"
                try:
                    result = self.run_one(rc, url, concurrency=workers_per_url[rc.model, url], cold_start=clean)
                except NotImplementedError as e:
                    with lock:
                        if _variant(rc) not in unimplemented:
                            unimplemented.add(_variant(rc))
                            print(f"skipping {' '.join(_variant(rc))}: not implemented ({e})", file=sys.stderr)
                    continue
                except (RuntimeError, subprocess.SubprocessError) as e:
                    if not clean:
                        raise
                    with lock:                  # no cold server: stop, don't fall back to a warm one
                        queue.clear()
                        failed.append(f"{tag} (restart: {e})")
                    print(f"({i}/{len(todo)}) {tag} restart failed, stopping: {e}", file=sys.stderr)
                    return
                except httpx.HTTPError as e:
                    print(f"({i}/{len(todo)}) {tag} server error, will retry next run: "
                          f"{type(e).__name__}: {e}", file=sys.stderr)
                    continue
                except Exception as e:      # never let one run end this worker and its queue
                    with lock:
                        failed.append(tag)
                    print(f"({i}/{len(todo)}) {tag} failed, will retry next run: "
                          f"{type(e).__name__}: {e}", file=sys.stderr)
                    traceback.print_exc(file=sys.stderr)
                    continue
                with lock:
                    results.append(self.results_path, result)
                    print(f"  ({i}/{len(todo)} done, {url})", file=sys.stderr)

        # one queue per model, one worker per server of that model
        threads = []
        urls = {m: (self.base_urls(m)[:1] if clean else self.base_urls(m)) for m in {rc.model for rc in todo}}
        workers_per_url = {(m, u): us.count(u) for m, us in urls.items() for u in us}
        for model in dict.fromkeys(rc.model for rc in todo):
            queue = [rc for rc in todo if rc.model == model]
            for url in urls[model]:
                threads.append(threading.Thread(target=worker, args=(url, queue)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if failed:
            print(f"{len(failed)} runs failed with an unexpected error (not recorded; rerun to retry): "
                  + ", ".join(failed), file=sys.stderr)

    def build_summaries(self, repo_size: str, model_key: str) -> int:
        """Cache one-line summaries per function (for L5 task_vs_summary)."""
        raise NotImplementedError

    def validate_retrieval(self, retrievers: list[str], repo_size: str = "S") -> str:
        """recall@1/3/8 of planted helpers per retriever, function and file level (query = task
        text, see retrieval/validate.py). Tasks with needed helpers that run on this repo."""
        from .retrieval.validate import validate
        registry = tomllib.loads(self._path(self.config["paths"]["helpers"]).read_text())
        naming = {h["qualname"]: h["naming"] for h in registry.get("helper", [])}
        decoys = {d["qualname"] for d in registry.get("decoy", [])}
        ws = load_workspace(self._path(self.config["repos"][repo_size]))
        tasks = [t for t in self.tasks.values() if t.needed_helpers
                 and (not t.repo_sizes or repo_size in t.repo_sizes)]
        out = [f"repo {repo_size}: {len(ws.chunks)} functions/classes, {len(ws.files)} files, "
               f"{len(tasks)} tasks with needed helpers; query = task text"]
        known = self.retrievers()
        for name in retrievers:
            make = known.get(name)
            if make is None:
                out.append(f"\n## {name}\nnot available (known: {', '.join(known)})")
                continue
            out.append(f"\n## {name}\n" + validate(make, ws, tasks, naming, decoys)[1])
        return "\n".join(out) + "\n"




def _seen_text(run_dir: Path) -> str | None:
    """What the levels put in front of the model, from a bundle: the <context> block plus every
    tool output (L7), i.e. the same text as ctx.text at run time. None if the bundle has no prompt."""
    prompt = run_dir / "prompt.json"
    if not prompt.is_file():
        return None
    parts = []
    for m in json.loads(prompt.read_text()):
        if m.get("role") == "user":           # the <context> block (as levels built it), else the turn
            text = m.get("content") or ""
            inner = re.search(r"<context>\n(.*?)\n</context>", text, re.S)
            parts.append(inner.group(1) if inner else text)
    transcript = run_dir / "transcript.jsonl"
    if transcript.is_file():
        for line in transcript.read_text().splitlines():
            m = json.loads(line)
            if m.get("role") == "tool":
                parts.append(m.get("content") or "")
    return "\n\n".join(parts)


def _had_tool_calls(transcript: Path, level: str) -> bool:
    """Single-shot runs: the saved raw message shows the server lifted tool calls out of the reply."""
    if level == "L7" or not transcript.is_file():
        return False
    for line in transcript.read_text().splitlines():
        entry = json.loads(line)
        if (entry.get("message") or {}).get("tool_calls"):
            return True
    return False


def _variant(rc: RunConfig) -> tuple[str, ...]:
    """Level plus the options that pick its implementation (e.g. L5 bm25 task_vs_code), so an
    unimplemented variant is skipped without skipping its implemented siblings."""
    return tuple(v for v in (rc.level, rc.retriever, rc.query_mode, rc.agent_variant) if v != "none")


def _runs_on(task: Task, size: str, sizes, all_sizes: list[str]) -> bool:
    """Whether `task` runs on repo `size` for a config whose repo_sizes is `sizes`: a list, or
    "smallest" = only the smallest repo the task fits (for levels whose prompt doesn't change with the
    rest of the repo, e.g. L0-L2: a task that fits S runs there once; an M-only task runs on M)."""
    fits = [s for s in all_sizes if not task.repo_sizes or s in task.repo_sizes]
    if sizes == "smallest":
        return bool(fits) and size == fits[0]
    return size in sizes and size in fits


def _level_options(rc: RunConfig) -> dict:
    """Only the variant options each level's build_context accepts."""
    if rc.level == "L4":
        return {"retriever": rc.retriever}
    if rc.level == "L5":
        return {"retriever": rc.retriever, "query_mode": rc.query_mode}
    if rc.level == "L6":
        return {"docs": rc.map_docs}
    if rc.level == "L7":
        return {"agent_variant": rc.agent_variant}
    return {}


def _helper_visible(task: Task, ctx: ContextBlock) -> bool | None:
    """Loose: every needed helper's def/class line appears in the context in any form - a source
    line, a signature-only map entry (L6, also after a decorator), or a tool output line such as
    grep's "path:N: def name(". Matches by bare name, so a same-named function elsewhere counts too."""
    if not task.needed_helpers:
        return None
    return all(re.search(rf"(?:^|[\s:@)])(?:async\s+)?(?:def|class)\s+{re.escape(q.rsplit('.', 1)[1])}\b",
                         ctx.text, re.M) for q in task.needed_helpers)


def _helper_body_visible(task: Task, ctx: ContextBlock, ws: Workspace) -> bool | None:
    """Strict: every needed helper's full source (signature, docstring and body, as in the repo)
    appears verbatim in the context, i.e. the model could read what it actually does."""
    if not task.needed_helpers:
        return None
    sources = {c.qualname: c.source for c in ws.chunks}
    return all(q in sources and sources[q].strip() in ctx.text for q in task.needed_helpers)


def _recall(task: Task, ctx: ContextBlock, ws: Workspace) -> float | None:
    """Share of needed helpers whose qualname, or the file that defines it, is among the retrieved
    items (functions for L5, paths for file-level retrieval)."""
    if not ctx.retrieved or not task.needed_helpers:
        return None
    files = {c.qualname: c.path for c in ws.chunks}
    def hit(q: str) -> bool:
        return q in ctx.retrieved or files.get(q) in ctx.retrieved
    return sum(hit(q) for q in task.needed_helpers) / len(task.needed_helpers)


def _result(*, rc, task, model_cfg, window, max_out, ctx, sampling, ws, label, ctx_fits, tests, tokens, latency,
            patch_path, error) -> RunResult:
    passed, total, reuse = tests
    tokens_in, tokens_out, peak = tokens
    return RunResult(
        run_id=rc.run_id, task_id=task.id, category=task.category, tier=task.tier, level=rc.level,
        retriever=rc.retriever, query_mode=rc.query_mode, agent_variant=rc.agent_variant,
        prompt_arm=rc.prompt_arm, model=model_cfg["model"], quant=model_cfg.get("quant", ""),
        embedder="none", seed=rc.seed, temperature=rc.temperature, sampling=sampling,
        repo_size=rc.repo_size, ctx_fits=ctx_fits, helper_in_context=_helper_visible(task, ctx),
        retrieval_recall=_recall(task, ctx, ws), label=label, reuse_passed=reuse, tests_passed=passed,
        tests_total=total, tokens_in=tokens_in, tokens_out=tokens_out, peak_ctx_tokens=peak,
        context_window=window, max_output_tokens=max_out, latency_s=round(latency, 2), tool_calls=0, patch_path=patch_path,
        error=error, helper_body_in_context=_helper_body_visible(task, ctx, ws))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _round(x: float | None) -> float | None:
    return None if x is None else round(x, 2)


def _set(result: RunResult, values: dict) -> None:
    for k, v in values.items():
        setattr(result, k, v)


def _log(r: RunResult) -> None:
    pct = 100 * r.peak_ctx_tokens / r.context_window
    reuse = "" if r.reuse_passed is None else f" reuse={'yes' if r.reuse_passed else 'no'}"
    print(f"[{r.task_id} {config_label(asdict(r))}] in={r.tokens_in} out={r.tokens_out} "
          f"peak={r.peak_ctx_tokens}/{r.context_window} ({pct:.1f}%) {r.latency_s}s tools={r.tool_calls} "
          f"-> {r.label} tests {r.tests_passed}/{r.tests_total}{reuse}"
          + (f" ({r.error})" if r.error else ""), file=sys.stderr)
