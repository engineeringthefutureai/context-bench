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
                        query, merges them round-robin by rank (every query's #1, then every #2, ...;
                        ranks, not scores, because BM25 scores don't compare across queries),
                        deduplicated, and keeps at most TOP_CHUNKS, the same budget as the other
                        modes. Details below.

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

import json
import re
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

# Appended to the normal user turn for the planning call only. Neutral: what to search for is the
# model's call; no mention of reuse, helpers or existing code.
PLAN_INSTRUCTION = ("First step, before making the change: list up to {n} search queries for finding code "
                    "in this repository that is relevant to it. The code they find will be shown to you, "
                    "and you will make the change in your next reply. For this step, reply with a JSON "
                    "list of strings only.")

# (task, L1-style context) -> (draft_text, tokens_in, tokens_out)
Drafter = Callable[[Task, ContextBlock], tuple[str, int, int]]
# (task, L1-style context, instruction) -> (raw planning reply, tokens_in, tokens_out, latency_s); the
# runner supplies it (same system prompt, prompt arm and thinking setting as the final call)
Planner = Callable[[Task, ContextBlock, str], tuple[str, int, int, float]]


def parse_queries(reply: str, limit: int = N_QUERIES) -> list[str]:
    """JSON list of strings, else one query per non-empty line with bullets/numbering stripped;
    at most `limit`, duplicates removed. [] if nothing usable. Lines that look like markup or code
    fences ("<...", "```") are skipped, so a reply that ignored the instruction yields []."""
    text = reply.strip()
    queries: list[str] = []
    decoder = json.JSONDecoder()
    for m in re.finditer(r"\[", text):          # the first '[' that starts a JSON list of strings
        try:
            data, _ = decoder.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(data, list) and (found := [q.strip() for q in data if isinstance(q, str) and q.strip()]):
            queries = found
            break
    if not queries:
        for line in text.splitlines():
            line = re.sub(r"^\s*(?:[-*\u2022]|\d+[.)])\s*", "", line).strip().strip('"').strip(",").strip()
            if line and not line.startswith(("<", "```", "[", "]")):
                queries.append(line)
    return list(dict.fromkeys(queries))[:limit]


def _merge(results: list[list[tuple[str, float]]]) -> list[tuple[str, float]]:
    """Round-robin by rank over the per-query hit lists, first occurrence wins."""
    merged: dict[str, float] = {}
    for rank in range(max((len(r) for r in results), default=0)):
        for hits in results:
            if rank < len(hits) and hits[rank][0] not in merged:
                merged[hits[rank][0]] = hits[rank][1]
    return list(merged.items())


def _make_retriever(name: str, ws: Workspace) -> Retriever:
    if name == "bm25":
        return BM25Retriever()
    if name in ws.retrievers:
        return ws.retrievers[name]()
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
    """Implemented: query_mode "task_vs_code" (query = task text) and "model_queries" (needs
    `planner`), with "bm25" or any retriever the
    runner supplies in ws.retrievers (e.g. "embed"). The target file's own chunks are not indexed:
    the target is shown in full."""
    if query_mode not in ("task_vs_code", "model_queries"):
        raise NotImplementedError(f"query_mode {query_mode!r}")
    path = task.target_file
    if path in ws.files:
        target = f"File: {path}\n```python\n{ws.files[path].rstrip()}\n```"
    else:
        target = f"File: {path}\n(This file does not exist yet.)"
    chunks = [c for c in ws.chunks if c.path != path]
    by_id = {c.qualname: c for c in chunks}
    r = _make_retriever(retriever, ws)
    r.index([c.qualname for c in chunks], [chunk_text(c) for c in chunks])

    extra = dict(extra_tokens_in=0, extra_tokens_out=0, extra_latency_s=0.0, transcript=[], note=None)
    if query_mode == "task_vs_code":
        queries = [task.prompt]
        hits = r.search(task.prompt, SEARCH_DEPTH)
    else:
        if planner is None:
            raise ValueError("model_queries needs a planner")
        instruction = PLAN_INSTRUCTION.format(n=N_QUERIES)
        reply, t_in, t_out, latency = planner(task, ContextBlock(text=target, included=[path]), instruction)
        queries = parse_queries(reply)
        if not queries:
            queries, extra["note"] = [task.prompt], "model_queries: no queries parsed; fell back to the task text"
        per_query = [r.search(q, K_PER_QUERY) for q in queries]
        hits = _merge(per_query)
        extra.update(extra_tokens_in=t_in, extra_tokens_out=t_out, extra_latency_s=latency,
                     transcript=[{"step": "planning", "instruction": instruction, "reply": reply,
                                  "queries": queries,
                                  "hits": {q: [d for d, _ in h] for q, h in zip(queries, per_query)}}])
    picked = _select(hits, by_id)
    text = target + ("\n\n" + _render(picked, ws) if picked else "")
    return ContextBlock(text=text, included=[path] + [c.qualname for c in picked],
                        retrieved=[c.qualname for c in picked], queries=queries, **extra)
