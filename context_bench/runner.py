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
import os
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
from .apply import (UnsafePath, apply_files, changed_files, format_status, make_sandbox, parse_files,
                    parse_files_lenient, unified_patch)
from .evaluate.hidden_tests import run_hidden_tests, run_repo_tests
from .levels import LEVELS
from .levels.l7_agentic import (FORMAT_RETRY, FORMAT_RETRY_EDIT, SEMANTIC_VARIANTS, RICH_VARIANTS, RepoTools, cacheable_prefix, run_agent,
                                tool_schemas, turn_prompts)
from .llm import ChatResult, OpenAICompatClient, env_value
from .metrics import (dead_modules, legacy_ctx_share, legacy_used, relevant_files, tool_output_breakdown,
                      usage_visible)
from .evaluate.patch_coverage import patch_coverage, tests_added
from .evaluate.hidden_tests import reuse_not_run
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
        self._since_restart: dict[tuple[str, str], int] = {}   # clean mode: runs since the last restart

    def embedder(self):
        """The configured [embedder], with an on-disk cache under [paths].cache/embeddings."""
        with self._client_lock:
            return self._make_embedder()

    def _make_embedder(self):
        if self._embedder is None:
            cfg = self.config["embedder"]
            self._embedder = CachedEmbedder(OpenAICompatEmbedder(cfg["base_url"], cfg["model"],
                                                                 ollama=cfg.get("api") == "ollama",
                                                                 context_tokens=cfg.get("context_tokens")),
                                            self._path(self.config["paths"]["cache"]) / "embeddings")
        return self._embedder

    def retrievers(self) -> dict:
        """Retriever name -> factory. "embed" uses the configured embedder and query_prefix."""
        prefix = self.config.get("embedder", {}).get("query_prefix", "")
        doc_prefix = self.config.get("embedder", {}).get("document_prefix", "")
        return {"bm25": BM25Retriever,
                "embed": lambda: EmbeddingRetriever(self.embedder(), query_prefix=prefix, document_prefix=doc_prefix,
                                                    max_chars=int(self.config.get("embedder", {}).get("max_chars", 10000)),
                                                    overlap_chars=int(self.config.get("embedder", {}).get("overlap_chars", 1000)))}

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

    def make_cold(self, model_key: str, url: str, level: str | None = None) -> str:
        """Clean mode, before a run's first model call: make the prefix cache cold. Restarts the server
        (restart_server) on the sweep's first run, every CBENCH_RESTART_EVERY runs (default 25; 1 =
        always; the agentic levels L7 and L8 use CBENCH_RESTART_EVERY_L7, default 12: multi-turn runs grow the vLLM workers fast, above all on repo L, so every agentic run
        starts on a fresh server), when the host's free memory is below CBENCH_RESTART_MIN_FREE_MB (default 6000; the
        vLLM workers grow in host RAM and only a restart gives it back; read from this machine's
        /proc/meminfo, so it assumes the server runs here), or when the server has no prefix-cache
        reset. Otherwise drops the prefix cache (/reset_prefix_cache), which takes milliseconds.
        Returns "restart" or "reset"."""
        key = (model_key, url)
        every = _restart_every(level)
        min_free = int(env_value("CBENCH_RESTART_MIN_FREE_MB") or 6000)
        since = self._since_restart.get(key)
        if since is not None and since + 1 < every and _free_mb() >= min_free:
            reset = getattr(self._client(model_key, url), "reset_prefix_cache", None)
            if reset is not None and reset():
                self._since_restart[key] = since + 1
                return "reset"
        self.restart_server(model_key, url)
        self._since_restart[key] = 0
        return "restart"

    def run_one(self, rc: RunConfig, base_url: str | None = None, concurrency: int = 1,
                cold_start: bool = False) -> RunResult:
        """One run; base_url picks one of the model's servers (default: the first); concurrency is
        how many runs share that server (recorded, so latency can be read in context).
        cold_start=True makes the prefix cache cold (make_cold: a restart or a cache reset) right before
        the run's first model call, so nothing cached survives from earlier runs; context building
        happens first, so a run whose level isn't implemented never costs a restart."""
        task = self.tasks[rc.task_id]
        model_cfg = self.config["models"][rc.model]
        gen = self.config["generation"]
        max_out, window = gen["max_output_tokens"], model_cfg["context_window"]
        client = self._client(rc.model, base_url)
        url = base_url or self.base_urls(rc.model)[0]
        restarted, cold_method = False, None

        def model_client():
            """The client for model calls; in clean mode the first call makes the cache cold."""
            nonlocal client, restarted, cold_method
            if cold_start and not restarted:
                cold_method = self.make_cold(rc.model, url, rc.level)
                client, restarted = self._client(rc.model, url), True
            return client
        perf = dict(thinking=rc.thinking, map_docs=rc.map_docs, prompt_version=rc.prompt_version, repo_hash=self.repo_hash(rc.repo_size), backend=self._backend[rc.model, base_url or self.base_urls(rc.model)[0]],
                    concurrency=concurrency)
        run_dir = self.runs_dir / rc.run_id

        with tempfile.TemporaryDirectory(prefix="cbench-") as tmp:
            sandbox = make_sandbox(self._path(self.config["repos"][rc.repo_size]), Path(tmp) / "repo")
            ws = load_workspace(sandbox)
            ws.retrievers = {"embed": self.retrievers()["embed"]}
            uses_embedder = rc.retriever == "embed" or rc.agent_variant in SEMANTIC_VARIANTS
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
            if rc.level in ("L7", "L8"):
                tools = RepoTools(sandbox, make_retriever=(ws.retrievers["embed"]
                                                           if rc.agent_variant in SEMANTIC_VARIANTS else None),
                                  rich=rc.agent_variant in RICH_VARIANTS,
                                  whole_files=rc.agent_variant == "edit")
                agent = run_agent(model_client(), messages, tools, agent_variant=rc.agent_variant,
                                  temperature=rc.temperature, seed=rc.seed, max_tokens=max_out,
                                  context_window=window, thinking=rc.thinking,
                                  failed_call_cost=rc.failed_call_cost, prompt_version=rc.prompt_version)
                chat = ChatResult(content=agent.content, reasoning=agent.reasoning,
                                  tokens_in=agent.tokens_in, tokens_out=agent.tokens_out,
                                  latency_s=agent.latency_s, finish_reason=agent.finish_reason)
                # helper_in_context for L7: the definition appeared in any tool output
                ctx_text_initial = ctx.text
                ctx.text += "\n\n" + "\n\n".join(tools.transcript)
                perf.update(tool_calls=agent.tool_calls, tool_errors=agent.tool_errors, format_retry=agent.format_retry,
                            failed_call_cost=rc.failed_call_cost, budget_used=agent.budget_used,
                            tokens_cached=agent.tokens_cached,
                            tokens_cacheable=agent.tokens_cacheable)
                if agent.drops is not None:     # selective
                    at_answer = ContextBlock(text=ctx_text_initial + "\n\n" + agent.answer_tool_text)
                    perf.update(drops=agent.drops, drop_calls=agent.drop_calls,
                                rereads_after_drop=agent.rereads_after_drop, hit_turn_cap=agent.hit_turn_cap,
                                dropped_tokens=agent.dropped_tokens, drop_saved_tokens=agent.drop_saved_tokens,
                                drop_refunds=agent.drop_refunds, drop_refund_clawbacks=agent.drop_refund_clawbacks,
                                helper_body_at_answer=_helper_body_visible(task, at_answer, ws),
                                legacy_share_at_answer=legacy_ctx_share(at_answer.text, self.legacy))
            else:
                chat = model_client().chat(messages, temperature=rc.temperature, seed=rc.seed, max_tokens=max_out,
                                           **({} if rc.thinking else {"thinking": False}))
            perf.update(finished_at=_now(), prefill_s=_round(chat.prefill_s), decode_s=_round(chat.decode_s),
                        cold_start=restarted, cold_method=cold_method)
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
            edit_tools = rc.prompt_arm == "edit_tools"    # L8: the agent already changed the sandbox
            if edit_tools:
                before, files = changed_files(self._path(self.config["repos"][rc.repo_size]), sandbox)
            if chat.finish_reason == "length":
                error = f"output hit max_output_tokens ({max_out})"
            elif not files:
                error = "no changes made with the edit tools" if edit_tools else "no <file> blocks in the response"
            patch = None
            try:
                if not edit_tools:
                    before = apply_files(sandbox, files)
                patch = unified_patch(before, files) or None
            except UnsafePath as e:
                error = f"unsafe path: {e}"
            passed, total, reuse, tests_out = run_hidden_tests(sandbox, task.hidden_tests, self.shared_dir)
            if files:
                r_passed, r_total, r_out = run_repo_tests(sandbox, self.shared_dir)
                perf.update(repo_tests_passed=r_passed, repo_tests_total=r_total)
                tests_out += "\n--- repo's own tests ---\n" + r_out
                if patch is not None:
                    perf.update(patch_coverage(sandbox, task.hidden_tests, self.shared_dir, before, files),
                                **tests_added(before, files))
        if agent:
            perf.update(tool_output_breakdown(agent.transcript, relevant_files(files, list(ws.files))))
        perf.update(self._protocol(task, rc.repo_size, _answer_text(chat.content, agent.transcript if agent else None),
                                   files))
        if rc.prompt_arm == "edit_tools":            # no reply format to check: the edits are the answer
            perf["format_status"] = "ok" if files else "no_code"
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
                            tests_output=tests_out, result=asdict(result),
                            files=files if rc.prompt_arm == "edit_tools" else None)
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
        helper visibility fields from their saved prompts and tool outputs (no model calls), and fill in
        the repo's-own-tests check for runs recorded before it existed: after
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
            if r.get("prompt_arm") == "edit_tools":     # L8: the changed files were saved with the run
                saved = self.runs_dir / r["run_id"] / "files.json"
                files = json.loads(saved.read_text()) if saved.is_file() else {}
            trans = self.runs_dir / r["run_id"] / "transcript.jsonl"
            agent_msgs = ([json.loads(x) for x in trans.read_text().splitlines() if x.strip()]
                          if r["level"] in AGENTIC_LEVELS and trans.is_file() else None)
            r.update(self._protocol(task, r["repo_size"], _answer_text(text, agent_msgs), files))
            if agent_msgs is not None:          # recorded for every agent variant (before: selective only)
                r["format_retry"] = any(m.get("role") == "user" and m.get("content") in (FORMAT_RETRY, FORMAT_RETRY_EDIT)
                                        for m in agent_msgs)
            if not files and _had_tool_calls(self.runs_dir / r["run_id"] / "transcript.jsonl", r["level"]):
                r["format_status"] = "tool_request"     # as run_one decided from the raw message
            if (seen := _seen_text(self.runs_dir / r["run_id"])) is not None:
                ctx, ws = ContextBlock(text=seen), workspaces[r["repo_size"]]
                r["helper_in_context"] = _helper_visible(task, ctx)
                r["helper_body_in_context"] = _helper_body_visible(task, ctx, ws)
                patch_file = self.runs_dir / r["run_id"] / "patch.diff"
                r.update(self._seen_metrics(task, seen, ws,
                                            patch_file.read_text() if patch_file.is_file() else None))
            if "repo_tests_total" not in r:
                r.update(self._repo_tests(r["repo_size"], files))
            tests_file = self.runs_dir / r["run_id"] / "tests.txt"
            if r.get("reuse_passed") is False and tests_file.is_file():
                section = tests_file.read_text().partition("--- reuse test ---")[2]
                if section and reuse_not_run(section.lstrip("\n")):
                    r["reuse_passed"] = None            # the reuse test couldn't run (code doesn't import)
            if "patch_coverage" not in r:
                r.update(self._patch_checks(task, r["repo_size"], files))
            transcript_file = self.runs_dir / r["run_id"] / "transcript.jsonl"
            if r["level"] == "L7" and "tool_out_chars" not in r and transcript_file.is_file():
                tr = [json.loads(line) for line in transcript_file.read_text().splitlines() if line.strip()]
                repo_files = list(workspaces[r["repo_size"]].files)
                r.update(tool_output_breakdown(tr, relevant_files(files, repo_files)))
            n += 1
        results.rewrite(self.results_path, rows)
        return n

    def _patch_checks(self, task: Task, repo_size: str, files: dict[str, str]) -> dict:
        """patch_coverage and tests_added for a saved reply (a fresh sandbox; None without file blocks)."""
        empty = dict.fromkeys(("patch_stmts", "patch_stmts_covered", "patch_coverage", "tests_added",
                               "test_files_changed"))
        if not files:
            return empty
        with tempfile.TemporaryDirectory(prefix="cbench-cover-") as tmp:
            box = make_sandbox(self._path(self.config["repos"][repo_size]), Path(tmp) / "repo")
            try:
                before = apply_files(box, files)
            except UnsafePath:
                return empty
            return {**patch_coverage(box, task.hidden_tests, self.shared_dir, before, files),
                    **tests_added(before, files)}

    def _repo_tests(self, repo_size: str, files: dict[str, str]) -> dict:
        """repo_tests_passed/total for a saved reply: apply its <file> blocks to a fresh sandbox and run
        the repo's own tests (None/None without file blocks)."""
        if not files:
            return {"repo_tests_passed": None, "repo_tests_total": None}
        with tempfile.TemporaryDirectory(prefix="cbench-repotests-") as tmp:
            box = make_sandbox(self._path(self.config["repos"][repo_size]), Path(tmp) / "repo")
            try:
                apply_files(box, files)
            except UnsafePath:
                return {"repo_tests_passed": None, "repo_tests_total": None}
            passed, total, _ = run_repo_tests(box, self.shared_dir)
        return {"repo_tests_passed": passed, "repo_tests_total": total}

    def backfill_cacheable(self, model_key: str) -> int:
        """tokens_cacheable (and the true per-turn peak) for L7 runs recorded before they existed or
        with peak = total input (a bug fixed 2026-11-21): rebuild every turn's request from the bundle (prompt.json + transcript.jsonl) and count its prompt tokens on `model_key`'s server
        (tokenizer only, no generation). Only rows of that model. Returns the count."""
        model = self.config["models"][model_key]["model"]
        client = self._client(model_key)
        rows, n = results.load(self.results_path), 0
        schema_cache: dict[tuple, int] = {}
        for r in rows:
            run = self.runs_dir / r["run_id"]
            if r["level"] != "L7" or r["model"] != model or not (run / "transcript.jsonl").is_file() \
                    or ("tokens_cacheable" in r and r["peak_ctx_tokens"] < r["tokens_in"]):
                continue
            messages = json.loads((run / "prompt.json").read_text())
            transcript = [json.loads(line) for line in (run / "transcript.jsonl").read_text().splitlines()]
            skey = (r["agent_variant"], r.get("prompt_version", 1))
            if skey not in schema_cache:
                schema_cache[skey] = client.count_tokens(json.dumps(tool_schemas(*skey)))
            total, prev, peak = 0, (0, None), 0
            for msgs, offer in turn_prompts(messages, transcript):
                tokens = client.count_prompt_tokens(msgs) + (schema_cache[skey] if offer else 0)
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
                                          prompt_version=PROMPT_VERSION,
                                          failed_call_cost=c.get("failed_call_cost"))
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
        clean=True: serial (one run at a time, each model's first server only) and the prefix cache is
        made cold before every run (make_cold: a cache reset, with a full restart on the first run,
        every CBENCH_RESTART_EVERY runs and when host memory runs low). A failed restart stops the sweep.
        Not clean (concurrent): when host memory drops below CBENCH_RESTART_MIN_FREE_MB and
        CBENCH_RESTART_CMD is set, no new run starts, the runs in flight finish, the server is restarted
        once and the sweep goes on (the vLLM workers grow in host RAM; nothing else restarts them). A
        failed restart is reported and the sweep continues without it."""
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
        # pre-flight: build the embedding indexes the queued runs need, so an embedder problem shows up
        # now instead of halfway through (cached vectors make this quick on later sweeps)
        needs_embed = sorted({rc.repo_size for rc in todo
                              if rc.retriever == "embed" or rc.agent_variant in SEMANTIC_VARIANTS},
                             key="SML".index)
        if needs_embed:
            print(f"pre-flight: embedding indexes for {', '.join(needs_embed)}", file=sys.stderr, flush=True)
            self.build_indexes(needs_embed)
        unimplemented: set[str] = set()
        failed: list[str] = []
        lock = threading.Lock()
        counter = iter(range(1, len(todo) + 1))
        # results/sweep_status.json: what this sweep still has queued and what is running now, for the
        # in-progress page (scripts/build_report.py). Rewritten on every change, removed at the end.
        status_path = self.results_path.parent / "sweep_status.json"
        status = {"pid": os.getpid(), "started_at": _now(), "clean": clean,
                  "runs": {rc.run_id: {**{k: v for k, v in asdict(rc).items()
                                          if k in ("task_id", "level", "retriever", "query_mode", "agent_variant",
                                                   "map_docs", "prompt_arm", "repo_size", "thinking",
                                                   "failed_call_cost", "prompt_version")},
                                       "state": "queued"} for rc in todo}}

        def set_state(run_id: str, state: str | None) -> None:
            """Under `lock`: mark a run running, or drop it (None) once recorded or given up."""
            if state is None:
                status["runs"].pop(run_id, None)
            elif run_id in status["runs"]:
                status["runs"][run_id]["state"] = state
            status["updated_at"] = _now()
            tmp = status_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(status))
            tmp.replace(status_path)

        with lock:
            set_state("", None)
        # concurrent sweeps: drain and restart the server when host memory runs low
        cond = threading.Condition()
        gate_state = {"active": 0, "restarting": False, "enabled": not clean and bool(env_value("CBENCH_RESTART_CMD")),
                      "agentic_since": None}   # agentic runs started since the last restart (None: none yet)
        min_free = int(env_value("CBENCH_RESTART_MIN_FREE_MB") or 6000)

        def gate(model: str, url: str, level: str) -> None:
            """Wait for a restart in progress; start one (after the runs in flight) when memory is low, or
            before an agentic run (L7/L8) every CBENCH_RESTART_EVERY_L7 agentic runs (default 1: each)."""
            if not gate_state["enabled"]:
                return
            agentic = level in AGENTIC_LEVELS
            with cond:
                while gate_state["restarting"]:
                    cond.wait()
                since = gate_state["agentic_since"]
                due = agentic and (since is None or since >= _restart_every(level))
                if _free_mb() >= min_free and not due:
                    gate_state["active"] += 1
                    if agentic:
                        gate_state["agentic_since"] = since + 1
                    return
                gate_state["restarting"] = True
                while gate_state["active"] > 0:
                    cond.wait()
            try:
                reason = (f"low memory ({_free_mb()} MB free < {min_free})" if _free_mb() < min_free
                          else f"before an agentic run ({level})")
                print(f"{reason}: restarting the model server", file=sys.stderr)
                self.restart_server(model, url)
            except Exception as e:                   # keep going on the old server, don't try again
                print(f"server restart failed, continuing without restarts: {e}", file=sys.stderr)
                gate_state["enabled"] = False
            finally:
                with cond:
                    gate_state["restarting"] = False
                    gate_state["active"] += 1
                    gate_state["agentic_since"] = 1 if agentic else 0
                    cond.notify_all()

        def release() -> None:
            if not gate_state["enabled"] and gate_state["active"] == 0:
                return
            with cond:
                gate_state["active"] = max(gate_state["active"] - 1, 0)
                cond.notify_all()

        def worker(url: str, queue: list[RunConfig]) -> None:
            while True:
                with lock:
                    if not queue:
                        return
                    rc = queue.pop(0)
                    if _variant(rc) in unimplemented:
                        set_state(rc.run_id, None)
                        continue
                    i = next(counter)
                    set_state(rc.run_id, "running")
                tag = f"[{rc.task_id} {config_label(asdict(rc))}]"
                gate(rc.model, url, rc.level)
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
                finally:
                    release()
                    with lock:
                        set_state(rc.run_id, None)
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
        status_path.unlink(missing_ok=True)
        if failed:
            print(f"{len(failed)} runs failed with an unexpected error (not recorded; rerun to retry): "
                  + ", ".join(failed), file=sys.stderr)

    def grade(self, task_ids: list[str] | None = None, dry_run: bool = False,
              repo_sizes: list[str] | None = None) -> int:
        """cbench grade: one blind judge per task and repo over the patches of the current prompt version
        on the fixture as it is now (grade.py), leaving out L7 runs of the original tool harness.
        Returns the number of runs graded."""
        from . import grade
        grading = self.config["grading"]
        rubric = self._path(grading["rubric"])
        version = (re.search(r"rubric_version:\s*(\S+)", rubric.read_text()) or [None, "?"])[1]
        rows = [r for r in results.load(self.results_path)
                if r.get("prompt_version", 1) == PROMPT_VERSION and r["task_id"] in self.tasks
                and (not task_ids or r["task_id"] in task_ids)
                and not (r["level"] == "L7" and r.get("failed_call_cost") is None)]   # superseded harness
        graded = 0
        for task_id in sorted({r["task_id"] for r in rows}):
            for size in sorted({r["repo_size"] for r in rows if r["task_id"] == task_id
                                and (not repo_sizes or r["repo_size"] in repo_sizes)}):
                mine = [r for r in rows if r["task_id"] == task_id and r["repo_size"] == size
                        and r.get("repo_hash") == self.repo_hash(size)]
                try:
                    n = grade.grade_task(self.tasks[task_id], size, mine, repo=self._path(self.config["repos"][size]),
                                         runs_dir=self.runs_dir, grades_path=self._path(self.config["paths"]["grades"]),
                                         grading=grading, rubric=rubric, rubric_version=version, dry_run=dry_run)
                except RuntimeError as e:  # one failed judge shouldn't end the pass; rerun with --task later
                    print(f"{task_id} @{size}: judge failed, skipped: {e}", file=sys.stderr)
                    continue
                print(f"{task_id} @{size}: {len(grade.collect_candidates(mine, self.runs_dir))} candidates"
                      + ("" if dry_run else f", {n} runs graded"), file=sys.stderr)
                graded += n
        return graded

    def grade_penalty(self, judge: str, task_ids: list[str] | None = None, dry_run: bool = False,
                      repo_sizes: list[str] | None = None, sample: int | None = None) -> int:
        """cbench grade --mode penalty: one judge call per distinct passing patch of the current prompt
        version on the fixture as it is now (penalty.py). Returns the number of runs graded."""
        from . import penalty
        grading = self.config["grading"]
        judges = grading.get("judges", {})
        if judge not in judges:
            raise SystemExit(f"unknown judge {judge!r}; [grading.judges] has: {', '.join(judges) or 'none'}")
        catalogue = self._path(grading["penalty_catalogue"])
        version = (re.search(r"catalogue_version:\s*(\S+)", catalogue.read_text()) or [None, "?"])[1]
        out_path = self._path(self.config["paths"].get("penalties", "results/penalties.jsonl"))
        rows = [r for r in results.load(self.results_path)
                if r.get("prompt_version", 1) == PROMPT_VERSION and r["task_id"] in self.tasks
                and (not task_ids or r["task_id"] in task_ids) and (not repo_sizes or r["repo_size"] in repo_sizes)
                and r.get("repo_hash") == self.repo_hash(r["repo_size"])
                and not (r["level"] == "L7" and r.get("failed_call_cost") is None)]
        latest = {r["run_id"]: r for r in rows}
        todo = penalty.pending(list(latest.values()), self.runs_dir, penalty.load(out_path), judge, version, sample)
        work = Path(grading["work_dir"]) / "penalty"
        graded = 0
        for i, (h, item) in enumerate(sorted(todo.items(), key=lambda kv: kv[1]["rows"][0]["task_id"]), 1):
            r0 = item["rows"][0]
            try:
                n = penalty.grade_patch(self.tasks[r0["task_id"]], r0["repo_size"], item,
                                        repo=self._path(self.config["repos"][r0["repo_size"]]), catalogue=catalogue,
                                        version=version, judge=judge, judge_command=judges[judge],
                                        work_dir=work, out_path=out_path, dry_run=dry_run)
            except (RuntimeError, subprocess.SubprocessError) as e:
                print(f"({i}/{len(todo)}) {r0['task_id']} @{r0['repo_size']} {h[:12]}: judge failed, skipped: {e}",
                      file=sys.stderr)
                continue
            print(f"({i}/{len(todo)}) {r0['task_id']} @{r0['repo_size']} {h[:12]}: {len(item['rows'])} runs"
                  + (" (earlier grade reused)" if item.get("reuse") else ""), file=sys.stderr)
            graded += n
        return graded

    def badges(self, results_dir: Path | None = None) -> int:
        """cbench badges: exact and intent badges for every recorded run (badges.py), written to
        <results>/badges.jsonl (rewritten in full; embeddings of the examples and sentences are cached)."""
        from . import badges
        rdir = results_dir or self.results_path.parent
        rows = list({r["run_id"]: r for r in results.load(rdir / "results.jsonl")}.values())
        recs = badges.compute(rows, rdir / "runs", self.embedder())
        tmp = rdir / "badges.jsonl.tmp"
        tmp.write_text("".join(json.dumps(x) + "\n" for x in recs))
        tmp.replace(rdir / "badges.jsonl")
        return len(recs)

    def build_dodge_prototypes(self) -> dict:
        """cbench badges --rebuild-prototypes: the WANTS TO EXPLORE prototypes from this results folder (first, the
        held-out check's training source) and every results/archive-*/ folder (badges.build_dodge_prototypes)."""
        from . import badges
        rdir = self.results_path.parent
        sources = []
        for d in [rdir] + sorted(p for p in rdir.glob("archive-*") if (p / "results.jsonl").is_file()):
            rows = list({r["run_id"]: r for r in results.load(d / "results.jsonl")}.values())
            sources.append((rows, d / "runs"))
        return badges.build_dodge_prototypes(sources, self.embedder())

    def build_indexes(self, repo_sizes: list[str]) -> list[str]:
        """cbench index --embeddings (and the sweep's pre-flight): embed everything the embedding
        retriever will index on these repos, before any run needs it: whole files (L4, as file_text) and
        functions/classes (L5 and the agents' semantic_search, as chunk_text). Vectors land in the on-disk
        cache, so runs only embed their queries. Raises on an embedder error. Returns one report line per
        index."""
        from .retrieval.base import chunk_text, file_text
        lines = []
        for size in repo_sizes:
            ws = load_workspace(self._path(self.config["repos"][size]))
            for kind, ids, texts in (
                    ("files", sorted(ws.files), [file_text(p, ws.files[p]) for p in sorted(ws.files)]),
                    ("chunks", [c.qualname for c in ws.chunks], [chunk_text(c) for c in ws.chunks])):
                start = time.monotonic()
                r = self.retrievers()["embed"]()
                r.index(ids, texts, split_long=kind == "files")      # functions are never split
                lines.append(f"{size} {kind}: {len(ids)} texts, {r.windowed} in windows "
                             f"({len(r.owner)} vectors), {time.monotonic() - start:.0f}s")
                print("  index " + lines[-1], file=sys.stderr, flush=True)
        return lines

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


AGENTIC_LEVELS = ("L7", "L8")


def _restart_every(level: str | None) -> int:
    """Runs per server restart: CBENCH_RESTART_EVERY_L7 (default 12; the memory guard restarts sooner when
    host memory runs low) for L7/L8, else CBENCH_RESTART_EVERY (default 25)."""
    if level in AGENTIC_LEVELS:
        return max(int(env_value("CBENCH_RESTART_EVERY_L7") or 12), 1)
    return max(int(env_value("CBENCH_RESTART_EVERY") or 25), 1)


def _free_mb() -> int:
    """MemAvailable of this machine in MB (a large number where /proc/meminfo doesn't exist)."""
    try:
        for line in open("/proc/meminfo"):
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 1 << 30


def _answer_text(final: str, agent_msgs: list[dict] | None) -> str:
    """The reply the output protocol is judged on: the final one, or for an agent whose final reply is empty
    (e.g. it answered a format retry with nothing) its last non-empty reply, so a malformed answer before
    the retry isn't hidden behind the empty one."""
    if final.strip() or not agent_msgs:
        return final
    for m in reversed(agent_msgs):
        if m.get("role") == "assistant" and (m.get("content") or "").strip():
            return m["content"]
    return final


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
    if rc.level in ("L7", "L8"):
        return {"agent_variant": rc.agent_variant, "prompt_version": rc.prompt_version}
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
