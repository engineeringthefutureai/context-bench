"""Core records shared by every module. RunResult is the results.jsonl schema."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

Level = Literal["L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8"]
Label = Literal[
    "reused", "duplicated", "inlined_dup", "hallucinated",
    "broken", "tool_error", "no_fit", "ok",
]
Category = Literal["reuse", "cross_file", "convention", "control"]


@dataclass(frozen=True)
class Task:
    id: str
    category: Category
    prompt: str                        # product language; never names the helper
    target_file: str                   # repo-relative path the change centers on
    needed_helpers: tuple[str, ...]    # qualnames, e.g. "shopkit.money.format_money"
    hidden_tests: Path                 # directory outside the repo; never shown to the model


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
    temperature: float = 0.2

    @property
    def run_id(self) -> str:
        """Stable hash of all fields; used to resume sweeps."""
        raise NotImplementedError


@dataclass
class RunResult:
    run_id: str
    task_id: str
    category: str
    level: str
    retriever: str
    query_mode: str
    agent_variant: str
    prompt_arm: str
    model: str
    quant: str
    embedder: str
    seed: int
    temperature: float
    repo_size: str
    ctx_fits: bool               # false -> label "no_fit"; never truncate silently
    helper_in_context: bool      # the causal split: was the needed helper's definition visible?
    retrieval_recall: float | None
    label: str
    needs_review: bool
    tests_passed: int
    tests_total: int
    equiv_score: float | None
    tokens_in: int
    tokens_out: int
    latency_s: float
    tool_calls: int
    patch_path: str | None
    error: str | None = None
