"""cbench validate-retrieval: where do the needed helpers rank when the query is the task text?

Query mode A (`task_vs_code`) only: the query is the task prompt, so this needs no model. Two
granularities, as L5 and L4 use them: functions (id = qualname) and files (id = path). The task's
target file is left out of the index, as the levels show it in full anyway. Recall@k is split by the
helper's naming style (registry: matching / opaque); the first decoy ranked above a helper is shown,
because a near-miss that outranks the real thing is what would reach the model."""
from __future__ import annotations

from typing import Callable

from ..types import Task
from ..workspace import Workspace
from .base import Retriever, chunk_text, file_text

KS = (1, 3, 8)
DEPTH = 50          # ranks beyond this print as ">50"


def helper_file(qualname: str, ws: Workspace) -> str | None:
    """Path of the file that defines `qualname`."""
    return next((c.path for c in ws.chunks if c.qualname == qualname), None)


def _rank(hits: list[tuple[str, float]], doc_id: str | None) -> int | None:
    return next((i + 1 for i, (d, _) in enumerate(hits) if d == doc_id), None)


def validate(make: Callable[[], Retriever], ws: Workspace, tasks: list[Task], naming: dict[str, str],
             decoys: set[str]) -> tuple[list[dict], str]:
    """Rows (one per task x needed helper) and a printable table with recall@k per naming style."""
    rows = []
    for task in tasks:
        chunks = [c for c in ws.chunks if c.path != task.target_file]
        funcs = make()
        funcs.index([c.qualname for c in chunks], [chunk_text(c) for c in chunks])
        files = make()
        paths = [p for p in sorted(ws.files) if p != task.target_file]
        files.index(paths, [file_text(p, ws.files[p]) for p in paths])
        fhits, phits = funcs.search(task.prompt, DEPTH), files.search(task.prompt, DEPTH)
        for q in task.needed_helpers:
            rank = _rank(fhits, q)
            above = [d for d, _ in fhits[:(rank or DEPTH) - 1] if d in decoys]
            rows.append({"task": task.id, "helper": q, "naming": naming.get(q, "?"),
                         "func_rank": rank, "file_rank": _rank(phits, helper_file(q, ws)),
                         "decoy_above": above[0] if above else None,
                         "top3": [d for d, _ in fhits[:3]]})
    return rows, _table(rows)


def _fmt(rank: int | None) -> str:
    return str(rank) if rank else f">{DEPTH}"


def _table(rows: list[dict]) -> str:
    out = ["| task | helper | naming | func rank | file rank | decoy above it | top 3 functions |",
           "|---|---|---|---|---|---|---|"]
    for r in rows:
        top = ", ".join(d.rsplit(".", 1)[1] for d in r["top3"])
        out.append(f"| {r['task']} | {r['helper'].rsplit('.', 1)[1]} | {r['naming']} | {_fmt(r['func_rank'])} "
                   f"| {_fmt(r['file_rank'])} | {(r['decoy_above'] or '').rsplit('.', 1)[-1]} | {top} |")
    out += ["", "| naming | n | " + " | ".join(f"func@{k}" for k in KS) + " | "
            + " | ".join(f"file@{k}" for k in KS) + " |", "|---|---|" + "---|" * (2 * len(KS))]
    for style in ("matching", "opaque", "all"):
        sel = [r for r in rows if style == "all" or r["naming"] == style]
        if not sel:
            continue
        cells = [f"{sum(1 for r in sel if r[col] and r[col] <= k)}/{len(sel)}"
                 for col in ("func_rank", "file_rank") for k in KS]
        out.append(f"| {style} | {len(sel)} | " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"
