"""Append-only results.jsonl with resume by run_id."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .types import RunResult


def done_run_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(line)["run_id"] for line in path.read_text().splitlines() if line.strip()}


def append(path: Path, result: RunResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(asdict(result)) + "\n")


def load(path: Path) -> list[dict]:
    """All result rows; if a run_id appears twice, the last line wins."""
    if not path.exists():
        return []
    rows = {}
    for line in path.read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["run_id"]] = r
    return list(rows.values())


def config_label(r: dict) -> str:
    """Short name for a configuration: level, its variant options, '+hint' for the reuse arm and
    '@M' etc. for any repo other than S."""
    parts = [r["level"]] + [r[k] for k in ("retriever", "query_mode", "agent_variant") if r[k] != "none"]
    return (" ".join(parts) + ("+hint" if r["prompt_arm"] != "base" else "")
            + (f" @{r['repo_size']}" if r["repo_size"] != "S" else ""))


def _ran_out(r: dict) -> bool:
    return (r.get("error") or "").startswith("output hit max_output_tokens")


def _cell(r: dict) -> str:
    mark = {"ok": "✅", "no_fit": "⛔", "tool_error": "⚠️"}.get(r["label"], "❌")
    out = f"{mark} {r['tests_passed']}/{r['tests_total']}"
    if _ran_out(r):
        out += " ✂"
    if r["reuse_passed"] is not None:
        out += " ♻️" if r["reuse_passed"] else " ✗reuse"
    if r["helper_in_context"]:
        out += " 👁"
    return out


def _table(header: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header),
            *("| " + " | ".join(str(c) for c in row) + " |" for row in rows)]


def report(path: Path) -> str:
    """Label counts and reuse rate per configuration, reuse when the helper was seen vs not
    seen, and usage per configuration: mean tokens in/out, max peak_ctx_tokens (and % of the
    window), latency, tool calls. Plus a task x configuration matrix. Markdown."""
    rows = load(path)
    if not rows:
        return f"No results in {path}."
    multi_model = len({r["model"] for r in rows}) > 1
    def label(r: dict) -> str:
        return config_label(r) + (f" [{r['model']}]" if multi_model else "")
    configs = list(dict.fromkeys(label(r) for r in sorted(rows, key=lambda r: (r["level"], label(r)))))
    tasks = sorted({(r["tier"], r["task_id"]) for r in rows})
    by_cell = {(r["task_id"], label(r)): r for r in rows}   # one seed for now; last seed wins otherwise

    out = ["## Results per task", "",
           "✅ all tests pass · ❌ a test failed · ⛔ no_fit · ⚠️ tool_error · ✂ ran out of output tokens · "
           "♻️ reused the helper · ✗reuse didn't · 👁 helper was in the context", ""]
    out += _table(["Task (tier)", *configs],
                  [[f"{t} ({tier})", *(_cell(by_cell[(t, c)]) if (t, c) in by_cell else "—" for c in configs)]
                   for tier, t in tasks])

    out += ["", "## Per configuration", ""]
    summary = []
    for c in configs:
        rs = [r for r in rows if label(r) == c]
        ru = [r for r in rs if r["reuse_passed"] is not None]
        peak = max(r["peak_ctx_tokens"] for r in rs)
        count = lambda lab: sum(r["label"] == lab for r in rs)
        summary.append([c, len(rs), count("ok"), count("broken"), count("no_fit"), count("tool_error"),
                        sum(map(_ran_out, rs)),
                        f"{sum(bool(r['reuse_passed']) for r in ru)}/{len(ru)}" if ru else "–",
                        sum(r["tokens_in"] for r in rs) // len(rs), sum(r["tokens_out"] for r in rs) // len(rs),
                        f"{peak} ({100 * peak / rs[0]['context_window']:.1f}%)",
                        f"{sum(r['latency_s'] for r in rs) / len(rs):.0f}s",
                        f"{sum(r['tool_calls'] for r in rs) / len(rs):.1f}"])
    out += _table(["Config", "runs", "ok", "broken", "no_fit", "tool_error", "ran out", "reuse ok",
                   "mean in", "mean out", "max peak", "mean latency", "mean tools"], summary)

    ru = [r for r in rows if r["reuse_passed"] is not None]
    if ru:
        out += ["", "## Reuse vs helper visibility (all configurations)", ""]
        vis = []
        for seen, name in ((True, "helper in context"), (False, "helper not in context")):
            group = [r for r in ru if bool(r["helper_in_context"]) is seen]
            vis.append([name, len(group), sum(bool(r["reuse_passed"]) for r in group),
                        sum(r["label"] == "ok" for r in group)])
        out += _table(["", "reuse runs", "reused", "tests ok"], vis)

    errors = [r for r in rows if r.get("error")]
    if errors:
        out += ["", "## Errors", ""]
        out += [f"- {r['task_id']} {label(r)}: {r['error']}" for r in sorted(errors, key=lambda r: (r['task_id'], label(r)))]
    return "\n".join(out) + "\n"
