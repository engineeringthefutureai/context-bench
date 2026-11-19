"""Core records shared by every module. RunResult is the results.jsonl schema."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

Level = Literal["L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]
Label = Literal["ok", "broken", "tool_error", "no_fit"]
Category = Literal["reuse", "cross_file", "convention", "control"]


@dataclass(frozen=True)
class Task:
    id: str
    category: Category
    prompt: str                        # product language; never names the helper
    target_file: str                   # repo-relative path the change centers on
    needed_helpers: tuple[str, ...]    # qualnames, e.g. "shopkit.money.format_money"
    hidden_tests: Path                 # directory outside the repo; never shown to the model
    tier: int                          # difficulty ladder 1..6 (see CLAUDE.md); orthogonal to category


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

    @property
    def run_id(self) -> str:
        """Stable hash of all fields; used to resume sweeps."""
        blob = json.dumps(asdict(self), sort_keys=True)
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
