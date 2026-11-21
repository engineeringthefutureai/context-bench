"""Core records shared by every module. RunResult is the results.jsonl schema."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

Level = Literal["L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]
Label = Literal["ok", "broken", "tool_error", "no_fit"]
Category = Literal["reuse", "cross_file", "convention", "control", "trap"]


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


@dataclass(frozen=True)
class RunConfig:
    task_id: str
    level: Level
    model: str
    seed: int
    repo_size: str = "S"
    retriever: str = "none"          # none | bm25 | embed | <your own>
    query_mode: str = "none"         # none | task_vs_code | task_vs_summary | draft_then_search  (L5)
    agent_variant: str = "none"      # none | grep | hybrid  (L8)
    prompt_arm: str = "base"         # base | reuse_hint
    temperature: float | None = None  # None = server default (not sent)
    thinking: bool = True            # False: chat_template_kwargs enable_thinking=false (Qwen templates)
    map_docs: bool = False           # L6 variant: show each entry's first docstring line in the map
    prompt_version: int = 1          # system prompt version (prompt.SYSTEM_PROMPTS); new runs: PROMPT_VERSION

    # Fields added after results existed: left out of run_id while at their default, so older runs
    # keep their ids.
    _LATE_DEFAULTS = {"thinking": True, "map_docs": False, "prompt_version": 1}

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
    helper_in_context: bool | None  # was every needed helper's definition visible? None: task needs none
    retrieval_recall: float | None
    label: str
    reuse_passed: bool | None    # reuse tasks: did test_reuse.py pass? None for other tasks
    tests_passed: int
    tests_total: int
    tokens_in: int               # summed over every model call in the run (L8: all turns; L5 draft included)
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
