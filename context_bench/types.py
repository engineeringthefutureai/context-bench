"""Core records shared by every module. RunResult is the results.jsonl schema."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

Level = Literal["L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7"]
Label = Literal["ok", "broken", "tool_error", "no_fit"]
Category = Literal["reuse", "cross_file", "convention", "control", "trap", "long"]   # long: a rule buried in a long function (repo L)


@dataclass(frozen=True)
class Task:
    id: str
    category: Category
    prompt: str                        # product language; never names the helper
    target_file: str                   # repo-relative path the change centers on
    needed_helpers: tuple[str, ...]    # qualnames, e.g. "shopkit.money.format_money"
    hidden_tests: Path                 # directory outside the repo; never shown to the model
    tier: int                          # difficulty ladder 1..6 (see CLAUDE.md); orthogonal to category
    repo_sizes: tuple[str, ...] = ()   # repos the task applies to; () = all (e.g. trap tasks: M only)


@dataclass(frozen=True)
class Chunk:
    """One function or class (methods included) extracted from the repo."""
    path: str            # repo-relative
    qualname: str        # module.Class.func
    kind: str            # "function" | "class"
    start_line: int
    end_line: int
    signature: str
    docstring: str
    source: str


@dataclass
class ContextBlock:
    """What a level puts inside <context>. The only thing that differs between levels."""
    text: str
    included: list[str] = field(default_factory=list)   # paths / qualnames placed in context
    retrieved: list[str] = field(default_factory=list)  # what retrieval returned (L4/L5), for recall
    queries: list[str] = field(default_factory=list)    # search queries used (L5 model_queries)
    extra_tokens_in: int = 0      # side generations, e.g. the draft in draft_then_search
    extra_tokens_out: int = 0
    extra_latency_s: float = 0.0  # side generations' wall time (counted in the run's latency)
    transcript: list[dict] = field(default_factory=list)  # side exchanges, saved in the bundle
    note: str | None = None       # something the run's error field should show (e.g. a fallback)


@dataclass(frozen=True)
class RunConfig:
    task_id: str
    level: Level
    model: str
    seed: int
    repo_size: str = "S"
    retriever: str = "none"          # none | bm25 | embed | <your own>
    query_mode: str = "none"         # none | task_vs_code | task_vs_summary | draft_then_search  (L5)
    agent_variant: str = "none"      # none | grep | hybrid  (L7)
    prompt_arm: str = "base"         # base | reuse_hint
    temperature: float | None = None  # None = server default (not sent)
    thinking: bool = True            # False: chat_template_kwargs enable_thinking=false (Qwen templates)
    map_docs: bool = False           # L6 variant: show each entry's first docstring line in the map
    prompt_version: int = 1          # system prompt version (prompt.SYSTEM_PROMPTS); new runs: PROMPT_VERSION
    failed_call_cost: float | None = None  # L7: budget charged per failed tool call (bad arguments, unknown
                                     # tool, a tool call written as text the server couldn't parse). Set:
                                     # each failure gets feedback (valid arguments / "couldn't parse")
                                     # and the loop goes on. None: the original harness (runs before
                                     # 2026-11-21): failures cost a full call, a text tool call ended the run.

    # Fields added after results existed: left out of run_id while at their default, so older runs
    # keep their ids.
    _LATE_DEFAULTS = {"thinking": True, "map_docs": False, "prompt_version": 1, "failed_call_cost": None}

    @property
    def run_id(self) -> str:
        """Stable hash of all fields; used to resume sweeps."""
        fields = {k: v for k, v in asdict(self).items()
                  if not (k in self._LATE_DEFAULTS and v == self._LATE_DEFAULTS[k])}
        blob = json.dumps(fields, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]


@dataclass
class RunResult:
    run_id: str
    task_id: str
    category: str
    tier: int
    level: str
    retriever: str
    query_mode: str
    agent_variant: str
    prompt_arm: str
    model: str
    quant: str
    embedder: str
    seed: int
    temperature: float | None    # what we asked for; None = server default
    sampling: dict               # what the server actually used (llm.server_sampling())
    repo_size: str
    ctx_fits: bool               # false -> label "no_fit"; never truncate silently
    helper_in_context: bool | None  # every needed helper's def line seen in any form (signature,
                                    #   source, tool output)? None: task needs none
    retrieval_recall: float | None
    label: str
    reuse_passed: bool | None    # reuse tasks: did test_reuse.py pass? None for other tasks
    tests_passed: int
    tests_total: int
    tokens_in: int               # summed over every model call in the run (L7: all turns; L5 draft included)
    tokens_out: int
    peak_ctx_tokens: int         # largest single prompt sent; vs context_window = how full the window got
    context_window: int
    max_output_tokens: int       # output cap per call (thinking included)
    latency_s: float
    tool_calls: int
    patch_path: str | None
    error: str | None = None
    # performance (added 2026-11-20; absent in older rows)
    thinking: bool = True               # was the model allowed to think (RunConfig.thinking)
    map_docs: bool = False              # L6 variant (RunConfig.map_docs)
    repo_hash: str | None = None        # fingerprint of the fixture repo's files when the run happened
    # output protocol (for learning; the label stays strict)
    format_status: str | None = None    # ok | bad_format | no_code | tool_request (apply.format_status)
    lenient_passed: int | None = None   # hidden tests on the leniently extracted code, when the strict
    lenient_total: int | None = None    #   parse found nothing but there was code (else None)
    lenient_reuse: bool | None = None
    backend: str = "unknown"            # llama.cpp | vllm | ollama | unknown
    concurrency: int = 1                # runs sharing this server at the time (sweep workers per URL)
    started_at: str | None = None       # ISO UTC, first model call started
    finished_at: str | None = None      # ISO UTC, last model call finished
    prefill_s: float | None = None      # prompt processing time, if the server reports it
    decode_s: float | None = None       # generation time, if the server reports it
    prompt_version: int = 1             # system prompt version (absent in older rows = 1)
    # input-token split for cost (added 2026-11-21; absent in older rows)
    tokens_cached: int | None = None    # server-reported prefix-cache hits, summed; None: not reported
    helper_body_in_context: bool | None = None  # strict visibility: each needed helper's full source
                                        #   was in view (helper_in_context also counts signatures)
    helper_usage_in_context: bool | None = None  # each needed helper seen imported or called (not just
                                        #   defined) somewhere in the context; None: task needs none
    legacy_ctx_share: float | None = None  # share of the context (chars) from dead code ([legacy] paths)
    legacy_used: bool | None = None     # the patch imports or references dead code; None: no patch
    cold_start: bool = False            # clean mode: the prefix cache was cold when this run started
    cold_method: str | None = None      # clean mode: "restart" (server restarted) or "reset" (prefix cache
                                        #   dropped via /reset_prefix_cache, server kept running)
    # embedder work in this run (added 2026-11-21; absent in older rows / runs without embeddings)
    embed_texts: int = 0                # texts sent to the embedding server (cache misses + queries)
    embed_tokens: int = 0               # tokens the embedding server reported for them
    embed_s: float = 0.0                # wall time waiting on the embedding server
    embed_cache_hits: int = 0           # texts served from the on-disk cache instead
    tokens_cacheable: int = 0           # input tokens that repeat an earlier turn's prompt exactly (tool
                                        #   loops: each turn resends the previous prompt), i.e. what any
                                        #   prefix-caching provider could bill at the cached rate
    # L7 tool-call accounting (added 2026-11-21; absent in older rows)
    tool_errors: int = 0                # failed tool calls (included in tool_calls)
    failed_call_cost: float | None = None  # RunConfig.failed_call_cost
    budget_used: float | None = None    # tool budget spent: successful calls + failed x failed_call_cost
    # regression check (added 2026-11-21; backfilled by cbench rescore). Label unaffected.
    repo_tests_passed: int | None = None  # the repo's own tests/ after the patch; None: no <file> blocks
    repo_tests_total: int | None = None
    # what the hidden tests verify of the patch (added 2026-11-22; backfilled by cbench rescore)
    patch_stmts: int | None = None      # statements the patch changed/added outside test files
    patch_stmts_covered: int | None = None  # of those, run by the hidden tests (correctness + reuse)
    patch_coverage: float | None = None # covered / stmts (None: no statements changed outside tests)
    tests_added: int | None = None      # new test functions the patch adds (the prompt never asks)
    test_files_changed: int | None = None
    # L7: what the tool output was made of (characters; added 2026-11-22; backfilled by cbench rescore)
    tool_out_chars: int | None = None
    read_whole_chars: int | None = None # read_file of a whole file
    read_part_chars: int | None = None  # ranged read_file, read_symbol
    search_chars: int | None = None     # semantic_search
    other_tool_chars: int | None = None # grep, list_files, find_references, drop replies, errors
    code_relevant_chars: int | None = None  # code from files the patch changed or imports from
    code_other_chars: int | None = None     # code from every other file
    # L7 selective (added 2026-11-22; None for other variants)
    drops: int | None = None            # tool results dropped (live tail only)
    drop_calls: int | None = None
    rereads_after_drop: int | None = None   # later reads/searches overlapping a dropped result
    dropped_tokens: int | None = None   # size of the dropped content minus its stubs (once)
    drop_refunds: int | None = None     # prompt v4: drops that gave back a quarter of a call
    drop_refund_clawbacks: int | None = None    # ...taken back because the dropped code was read again
    drop_saved_tokens: int | None = None    # input tokens avoided over all later requests;
                                        #   tokens_in + drop_saved_tokens = the same run without drops
    hit_turn_cap: bool | None = None    # MAX_TURNS backstop reached
    helper_body_at_answer: bool | None = None   # helper_body_in_context, counting only what was still in
                                        #   the conversation at the final turn (stubs, not dropped bodies)
    legacy_share_at_answer: float | None = None # legacy_ctx_share, same basis
