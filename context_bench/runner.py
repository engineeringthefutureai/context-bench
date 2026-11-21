"""Orchestration. One run:

  1. copy the repo into a fresh sandbox
  2. build the context with LEVELS[level](task, workspace, **variant options)
  3. render the fixed prompt; count tokens; if tokens + max_output_tokens > context_window
     -> ctx_fits=false, label no_fit, stop
  4. call the model (L8: run the tool loop, which applies the same fit check before every turn);
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
import re
import sys
import tempfile
from datetime import datetime, timezone
import threading
import tomllib
from dataclasses import asdict
from pathlib import Path

import httpx

from . import bundle, results
from .apply import (UnsafePath, apply_files, format_status, make_sandbox, parse_files,
                    parse_files_lenient, unified_patch)
from .evaluate.hidden_tests import run_hidden_tests
from .levels import LEVELS
from .llm import OpenAICompatClient
from .prompt import render
from .results import config_label
from .tasks import load_tasks
from .types import ContextBlock, RunConfig, RunResult, Task
from .workspace import load_workspace


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
        self.default_model = self.config["sweep"]["models"][0]
        self._client_factory = client_factory or (
            lambda m, url: OpenAICompatClient(url, m["model"]))
        self._clients: dict[tuple[str, str], object] = {}
        self._sampling: dict[str, dict] = {}
        self._backend: dict[tuple[str, str], str] = {}
        self._repo_hash: dict[str, str] = {}

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
        if (model_key, url) not in self._clients:
            self._clients[model_key, url] = self._client_factory(self.config["models"][model_key], url)
            client = self._clients[model_key, url]
            if model_key not in self._sampling:
                self._sampling[model_key] = client.server_sampling()
            self._backend[model_key, url] = client.backend() if hasattr(client, "backend") else "unknown"
        return self._clients[model_key, url]

    def run_one(self, rc: RunConfig, base_url: str | None = None, concurrency: int = 1) -> RunResult:
        """One run; base_url picks one of the model's servers (default: the first); concurrency is
        how many runs share that server (recorded, so latency can be read in context)."""
        task = self.tasks[rc.task_id]
        model_cfg = self.config["models"][rc.model]
        gen = self.config["generation"]
        max_out, window = gen["max_output_tokens"], model_cfg["context_window"]
        client = self._client(rc.model, base_url)
        perf = dict(thinking=rc.thinking, map_docs=rc.map_docs, repo_hash=self.repo_hash(rc.repo_size), backend=self._backend[rc.model, base_url or self.base_urls(rc.model)[0]],
                    concurrency=concurrency)
        run_dir = self.runs_dir / rc.run_id

        with tempfile.TemporaryDirectory(prefix="cbench-") as tmp:
            sandbox = make_sandbox(self._path(self.config["repos"][rc.repo_size]), Path(tmp) / "repo")
            ctx = LEVELS[rc.level](task, load_workspace(sandbox), **_level_options(rc))
            messages = render(task, ctx, rc.prompt_arm)
            prompt_tokens = client.count_prompt_tokens(messages)
            base = dict(rc=rc, task=task, model_cfg=model_cfg, window=window, max_out=max_out, ctx=ctx,
                        sampling=self._sampling[rc.model])

            if prompt_tokens + max_out > window:
                result = _result(**base, label="no_fit", ctx_fits=False, tests=(0, 0, None),
                                 tokens=(prompt_tokens, 0, prompt_tokens), latency=0.0, patch_path=None,
                                 error=f"prompt {prompt_tokens} + max_output {max_out} > window {window}")
                _set(result, perf)
                bundle.write_bundle(run_dir, patch=None, messages=messages, response="", reasoning=None,
                                    transcript=None, tests_output="", result=asdict(result))
                _log(result)
                return result

            if rc.level == "L8":
                raise NotImplementedError("L8 tool loop is not implemented yet")
            perf["started_at"] = _now()
            chat = client.chat(messages, temperature=rc.temperature, seed=rc.seed, max_tokens=max_out,
                               **({} if rc.thinking else {"thinking": False}))
            perf.update(finished_at=_now(), prefill_s=_round(chat.prefill_s), decode_s=_round(chat.decode_s))

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

        label = "ok" if total > 0 and passed == total else "broken"
        result = _result(**base, label=label, ctx_fits=True, tests=(passed, total, reuse),
                         tokens=(chat.tokens_in + ctx.extra_tokens_in, chat.tokens_out + ctx.extra_tokens_out,
                                 max(prompt_tokens, chat.tokens_in)),
                         latency=chat.latency_s,
                         patch_path=str((run_dir / "patch.diff").relative_to(self.root)) if patch else None,
                         error=error)
        _set(result, perf)
        bundle.write_bundle(run_dir, patch=patch, messages=messages, response=chat.content,
                            reasoning=chat.reasoning, transcript=None, tests_output=tests_out,
                            result=asdict(result))
        _log(result)
        return result

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
        """Recompute the output-protocol fields of recorded runs from their saved responses (no model
        calls): after the parser or classifier changes, or for runs recorded before they existed.
        Skips runs whose repo has changed since (repo_hash). Rewrites results.jsonl; returns the count."""
        rows, n = results.load(self.results_path), 0
        for r in rows:
            response = self.runs_dir / r["run_id"] / "response.txt"
            task = self.tasks.get(r["task_id"])
            if r["label"] == "no_fit" or task is None or not response.is_file() \
                    or r.get("repo_hash") not in (None, self.repo_hash(r["repo_size"])):
                continue
            text = response.read_text()
            r.update(self._protocol(task, r["repo_size"], text, parse_files(text)))
            n += 1
        results.rewrite(self.results_path, rows)
        return n

    def configs(self, levels: list[str] | None = None, models: list[str] | None = None,
                repo_sizes: list[str] | None = None, thinking: bool | None = None) -> list[RunConfig]:
        """Expand [[config]] entries x models x repo sizes x seeds x tasks into RunConfigs.
        A config's optional `categories` and `tiers` keep only matching tasks (e.g. L0: tier 1).
        `levels` / `repo_sizes` keep only those (cbench run --level / --repo-size); `models` replaces the
        sweep's models. Thinking comes from [generation].thinking (default on), a config's own
        `thinking` key, or `thinking` here, which overrides both (cbench run --thinking/--no-thinking)."""
        sweep = self.config["sweep"]
        out = []
        for c in self.config.get("config", []):
            if levels and c["level"] not in levels:
                continue
            tasks = [t for t in sorted(self.tasks.values(), key=lambda t: (t.tier, t.id))
                     if ("categories" not in c or t.category in c["categories"])
                     and ("tiers" not in c or t.tier in c["tiers"])]
            for model in models or c.get("models", sweep["models"]):
                sizes = c.get("repo_sizes", sweep["default_repo_sizes"])
                for size in [s for s in sizes if not repo_sizes or s in repo_sizes]:
                    for seed in sweep["seeds"]:
                        out += [RunConfig(task_id=t.id, level=c["level"], model=model, seed=seed,
                                          repo_size=size, retriever=c.get("retriever", "none"),
                                          query_mode=c.get("query_mode", "none"),
                                          agent_variant=c.get("agent_variant", "none"),
                                          prompt_arm=c.get("prompt_arm", "base"),
                                          temperature=self.config["generation"].get("temperature"),
                                          thinking=(thinking if thinking is not None else
                                                    c.get("thinking", self.config["generation"].get("thinking", True))),
                                          map_docs=c.get("map_docs", False))
                                for t in tasks]
        return out

    def sweep(self, levels: list[str] | None = None, dry_run: bool = False,
              models: list[str] | None = None, repo_sizes: list[str] | None = None,
              thinking: bool | None = None) -> None:
        """Run every config not already in results.jsonl (or whose repo has changed since: a recorded
        repo_hash that differs from the fixture now); failures aren't written, so they retry.
        A level that isn't implemented yet is skipped (reported once); a server error (timeout,
        connection, HTTP status) skips that run, so the next `cbench run` retries it."""
        recorded = {r["run_id"]: r.get("repo_hash") for r in results.load(self.results_path)}
        todo = [rc for rc in self.configs(levels, models, repo_sizes, thinking)
                if rc.run_id not in recorded
                or recorded[rc.run_id] not in (None, self.repo_hash(rc.repo_size))]
        done = recorded.keys()
        print(f"{len(todo)} runs to do ({len(done)} already in {self.results_path.name})", file=sys.stderr)
        if dry_run:
            for rc in todo:
                print(f"  {rc.task_id} {config_label(asdict(rc))}", file=sys.stderr)
            return
        unimplemented: set[str] = set()
        lock = threading.Lock()
        counter = iter(range(1, len(todo) + 1))

        def worker(url: str, queue: list[RunConfig]) -> None:
            while True:
                with lock:
                    if not queue:
                        return
                    rc = queue.pop(0)
                    if rc.level in unimplemented:
                        continue
                    i = next(counter)
                tag = f"[{rc.task_id} {config_label(asdict(rc))}]"
                try:
                    result = self.run_one(rc, url, concurrency=workers_per_url[rc.model, url])
                except NotImplementedError as e:
                    with lock:
                        if rc.level not in unimplemented:
                            unimplemented.add(rc.level)
                            print(f"skipping {rc.level}: not implemented ({e})", file=sys.stderr)
                    continue
                except httpx.HTTPError as e:
                    print(f"({i}/{len(todo)}) {tag} server error, will retry next run: "
                          f"{type(e).__name__}: {e}", file=sys.stderr)
                    continue
                with lock:
                    results.append(self.results_path, result)
                    print(f"  ({i}/{len(todo)} done, {url})", file=sys.stderr)

        # one queue per model, one worker per server of that model
        threads = []
        workers_per_url = {(m, u): self.base_urls(m).count(u) for m in {rc.model for rc in todo}
                           for u in self.base_urls(m)}
        for model in dict.fromkeys(rc.model for rc in todo):
            queue = [rc for rc in todo if rc.model == model]
            for url in self.base_urls(model):
                threads.append(threading.Thread(target=worker, args=(url, queue)))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    def build_summaries(self, repo_size: str, model_key: str) -> int:
        """Cache one-line summaries per function (for L5 task_vs_summary)."""
        raise NotImplementedError

    def validate_retrieval(self, retrievers: list[str], repo_size: str = "S") -> str:
        """recall@1/3/8 of planted helpers per retriever, function and file level."""
        raise NotImplementedError


def _level_options(rc: RunConfig) -> dict:
    """Only the variant options each level's build_context accepts."""
    if rc.level == "L4":
        return {"retriever": rc.retriever}
    if rc.level == "L5":
        return {"retriever": rc.retriever, "query_mode": rc.query_mode}
    if rc.level == "L6":
        return {"docs": rc.map_docs}
    if rc.level == "L8":
        return {"agent_variant": rc.agent_variant}
    return {}


def _helper_visible(task: Task, ctx: ContextBlock) -> bool | None:
    """Every needed helper's definition (def/class line) appears in the context text."""
    if not task.needed_helpers:
        return None
    return all(re.search(rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(q.rsplit('.', 1)[1])}\b",
                         ctx.text, re.M) for q in task.needed_helpers)


def _recall(task: Task, ctx: ContextBlock) -> float | None:
    """Share of needed helpers whose qualname or module file is among the retrieved items."""
    if not ctx.retrieved or not task.needed_helpers:
        return None
    def hit(q: str) -> bool:
        module_file = q.rsplit(".", 1)[0].replace(".", "/") + ".py"
        return q in ctx.retrieved or module_file in ctx.retrieved
    return sum(hit(q) for q in task.needed_helpers) / len(task.needed_helpers)


def _result(*, rc, task, model_cfg, window, max_out, ctx, sampling, label, ctx_fits, tests, tokens, latency,
            patch_path, error) -> RunResult:
    passed, total, reuse = tests
    tokens_in, tokens_out, peak = tokens
    return RunResult(
        run_id=rc.run_id, task_id=task.id, category=task.category, tier=task.tier, level=rc.level,
        retriever=rc.retriever, query_mode=rc.query_mode, agent_variant=rc.agent_variant,
        prompt_arm=rc.prompt_arm, model=model_cfg["model"], quant=model_cfg.get("quant", ""),
        embedder="none", seed=rc.seed, temperature=rc.temperature, sampling=sampling,
        repo_size=rc.repo_size, ctx_fits=ctx_fits, helper_in_context=_helper_visible(task, ctx),
        retrieval_recall=_recall(task, ctx), label=label, reuse_passed=reuse, tests_passed=passed,
        tests_total=total, tokens_in=tokens_in, tokens_out=tokens_out, peak_ctx_tokens=peak,
        context_window=window, max_output_tokens=max_out, latency_s=round(latency, 2), tool_calls=0, patch_path=patch_path,
        error=error)


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
