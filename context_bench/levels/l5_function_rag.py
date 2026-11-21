"""L5 — function-level RAG. Target file + top-8 functions/classes from other files.

Each retrieved chunk is shown with its file path, the file's import header, signature,
docstring and body.

Variants:
  retriever  = "bm25" | "embed" (or any Retriever implementation)
  query_mode =
    "task_vs_code"      task text searched against function source (naive baseline)
    "task_vs_summary"   task text searched against one-line natural-language summaries of each
                        function (generated once at index time and cached)
    "draft_then_search" the model first drafts the code it would write (with L1 context); the draft
                        is searched against the function index; hits go into context. Draft tokens
                        count toward cost (extra_tokens_in / extra_tokens_out).
    "model_queries"     model-planned retrieval: one planning call where the model, seeing the task
                        and the target file only (L1 context), writes up to N_QUERIES search queries;
                        the harness runs each through the retriever, takes the top K_PER_QUERY per
                        query, merges them (deduplicated, ranked by best score) and keeps TOP_CHUNKS,
                        the same budget as the other modes. Details below.

Record retrieved qualnames in ContextBlock.retrieved so recall can be computed.

model_queries in detail
  Planning call: the same system prompt as every run; the user turn is the L1 context and the task,
  followed by PLAN_INSTRUCTION. PLAN_INSTRUCTION is neutral on purpose: it asks for search queries
  for code relevant to the change and never mentions reuse, helpers or existing code (that would
  smuggle in the reuse_hint arm). Thinking follows [generation].thinking, like the final call.
  Parsing: a JSON list of strings, else one query per non-empty line (bullets/numbering stripped);
  at most N_QUERIES are used. If nothing parses, fall back to the task text as the only query and
  say so in the run's error, so the run completes and the fallback stays visible.
  Cost: the planning call's tokens go into extra_tokens_in / extra_tokens_out (as draft_then_search);
  latency covers both calls. The planning exchange and the parsed queries are saved in the bundle
  (transcript.jsonl) so query quality can be inspected; ContextBlock.queries holds the queries.
  The final call uses the normal template: only <context> differs (target file + retrieved chunks,
  same format as the other modes).
  Measured: retrieval_recall, directly comparable with task_vs_code on the same tasks; queries per run
  and which query found each needed helper (from the bundle).
  Later, as a separate mode if wanted: planning with the L6 map in view ("map_queries"), so each mode
  changes one thing.
"""
from __future__ import annotations

from typing import Callable

from ..chunking import import_header
from ..retrieval.base import Retriever, chunk_text
from ..retrieval.bm25 import BM25Retriever
from ..types import Chunk, ContextBlock, Task
from ..workspace import Workspace

TOP_CHUNKS = 8
SEARCH_DEPTH = 40      # hits fetched before overlap filtering fills the TOP_CHUNKS budget
RETRIEVED_HEADER = ("Retrieved code from other files (the most relevant functions and classes found "
                    "by searching the repository):")
N_QUERIES = 5          # model_queries: queries used from the planning reply
K_PER_QUERY = 3        # model_queries: hits taken per query before merging

PLAN_INSTRUCTION = ("Before making the change, list up to {n} search queries for finding code in this "
                    "repository that is relevant to it. Reply with a JSON list of strings only.")

# (task, L1-style context) -> (draft_text, tokens_in, tokens_out)
Drafter = Callable[[Task, ContextBlock], tuple[str, int, int]]
# (task, L1-style context) -> (raw planning reply, tokens_in, tokens_out); the runner supplies it and
# records the exchange in the bundle
Planner = Callable[[Task, ContextBlock], tuple[str, int, int]]


def parse_queries(reply: str, limit: int = N_QUERIES) -> list[str]:
    """JSON list of strings, else one query per non-empty line with bullets/numbering stripped;
    at most `limit`, duplicates removed. [] if nothing usable."""
    raise NotImplementedError


def _make_retriever(name: str) -> Retriever:
    if name == "bm25":
        return BM25Retriever()
    raise NotImplementedError(f"retriever {name!r}")


def _overlaps(a: Chunk, b: Chunk) -> bool:
    """Same file and one span inside the other (a class and one of its methods)."""
    return a.path == b.path and (a.start_line <= b.start_line <= b.end_line <= a.end_line
                                 or b.start_line <= a.start_line <= a.end_line <= b.end_line)


def _select(hits: list[tuple[str, float]], by_id: dict[str, Chunk]) -> list[Chunk]:
    """Best TOP_CHUNKS in rank order, skipping a chunk that overlaps one already taken, so a class
    and its method don't both spend the budget on the same lines."""
    picked: list[Chunk] = []
    for qualname, _ in hits:
        c = by_id[qualname]
        if not any(_overlaps(c, p) for p in picked):
            picked.append(c)
        if len(picked) == TOP_CHUNKS:
            break
    return picked


def _render(picked: list[Chunk], ws: Workspace) -> str:
    """Grouped by file in order of first appearance: path, the file's import header once, then each
    chunk's source (signature, docstring, body) with its line numbers."""
    by_file: dict[str, list[Chunk]] = {}
    for c in picked:
        by_file.setdefault(c.path, []).append(c)
    parts = [RETRIEVED_HEADER]
    for path, chunks in by_file.items():
        body = []
        if header := import_header(ws.files[path]):
            body.append(header)
        body += [f"# lines {c.start_line}-{c.end_line}\n{c.source}" for c in chunks]
        parts.append(f"File: {path}\n```python\n" + "\n\n".join(body) + "\n```")
    return "\n\n".join(parts)


def build_context(task: Task, ws: Workspace, retriever: str = "bm25",
                  query_mode: str = "task_vs_code", drafter: Drafter | None = None,
                  planner: Planner | None = None) -> ContextBlock:
    """Implemented: query_mode "task_vs_code" (query = task text). The target file's own chunks are
    not indexed: the target is shown in full."""
    if query_mode != "task_vs_code":
        raise NotImplementedError(f"query_mode {query_mode!r}")
    path = task.target_file
    if path in ws.files:
        target = f"File: {path}\n```python\n{ws.files[path].rstrip()}\n```"
    else:
        target = f"File: {path}\n(This file does not exist yet.)"
    chunks = [c for c in ws.chunks if c.path != path]
    r = _make_retriever(retriever)
    r.index([c.qualname for c in chunks], [chunk_text(c) for c in chunks])
    picked = _select(r.search(task.prompt, SEARCH_DEPTH), {c.qualname: c for c in chunks})
    text = target + ("\n\n" + _render(picked, ws) if picked else "")
    return ContextBlock(text=text, included=[path] + [c.qualname for c in picked],
                        retrieved=[c.qualname for c in picked], queries=[task.prompt])
