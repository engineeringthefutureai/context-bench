"""Append-only results.jsonl with resume by run_id."""
from __future__ import annotations

from .prompt import PROMPT_VERSION

import json
from datetime import datetime
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


def rewrite(path: Path, rows: list[dict]) -> None:
    """Replace the file with these rows (one per run_id), via a temp file so a crash can't truncate it."""
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows))
    tmp.replace(path)


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
    """Short name for a configuration: level, its variant options, '+hint' for the reuse arm,
    '@M' etc. for any repo other than S, and ' p1' etc. for rows made with an older system prompt."""
    parts = [r["level"]] + [r[k] for k in ("retriever", "query_mode", "agent_variant") if r[k] != "none"]
    return (" ".join(parts) + ("+hint" if r["prompt_arm"] != "base" else "")
            + ("+docs" if r.get("map_docs") else "")
            + (" nothink" if r.get("thinking") is False else "")
            + (f" @{r['repo_size']}" if r["repo_size"] != "S" else "")
            + (f" p{r.get('prompt_version', 1)}" if r.get("prompt_version", 1) != PROMPT_VERSION else ""))


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
    status = r.get("format_status")
    if status == "bad_format":
        lenient = f" →{r['lenient_passed']}/{r['lenient_total']}" if r.get("lenient_total") else ""
        out += f" (fmt{lenient})"
    elif status in ("no_code", "tool_request"):
        out += " (asked)" if status == "tool_request" else " (no code)"
    return out


def _busy_seconds(rows: list[dict]) -> float:
    """Wall time during which at least one of these runs was calling the model: the union of their
    [started_at, finished_at] spans, so gaps between sweeps don't count."""
    spans = sorted((datetime.fromisoformat(r["started_at"]), datetime.fromisoformat(r["finished_at"]))
                   for r in rows if r.get("started_at") and r.get("finished_at"))
    total, end = 0.0, None
    for start, finish in spans:
        if end is None or start > end:
            total += (finish - start).total_seconds()
            end = finish
        elif finish > end:
            total += (finish - end).total_seconds()
            end = finish
    return total


def _rate(tokens: int, seconds: float) -> str:
    return f"{tokens / seconds:.1f}" if seconds else "–"


def performance(rows: list[dict]) -> list[str]:
    """Per model and backend: tokens/s per request (output tokens / model-call time), combined
    tokens/s (all output / busy wall time, which counts concurrent requests once), concurrency, and
    the server's own prefill/decode split where it reports one (llama.cpp)."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for r in rows:
        if r["label"] != "no_fit" and r["latency_s"]:
            groups.setdefault((r["model"], r.get("backend", "unknown")), []).append(r)
    table = []
    for (model, backend), rs in sorted(groups.items()):
        out = sum(r["tokens_out"] for r in rs)
        timed = [r for r in rs if r.get("started_at")]
        dec = [r for r in rs if r.get("decode_s")]
        pre = [r for r in rs if r.get("prefill_s")]
        conc = sorted({r.get("concurrency", 1) for r in rs})
        table.append([model, backend, len(rs), "/".join(map(str, conc)), f"{out:,}",
                      _rate(out, sum(r["latency_s"] for r in rs)),
                      _rate(sum(r["tokens_out"] for r in timed), _busy_seconds(timed)) if timed else "–",
                      _rate(sum(r["tokens_in"] for r in pre), sum(r["prefill_s"] for r in pre)) if pre else "–",
                      _rate(sum(r["tokens_out"] for r in dec), sum(r["decode_s"] for r in dec)) if dec else "–"])
    return _table(["Model", "backend", "runs", "concurrency", "output tokens", "tok/s per request",
                   "tok/s combined", "prefill tok/s", "decode tok/s"], table)


def _table(header: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header),
            *("| " + " | ".join(str(c) for c in row) + " |" for row in rows)]


def _cache_share(rs: list[dict]) -> str:
    """Share of input tokens that repeat an earlier turn's prompt (tool loops), plus the server's
    actual cache hits when every run reported them."""
    total = sum(r["tokens_in"] for r in rs) or 1
    out = f"{100 * sum(r.get('tokens_cacheable', 0) for r in rs) / total:.0f}%"
    if all(r.get("tokens_cached") is not None for r in rs):
        out += f" (hit {100 * sum(r['tokens_cached'] for r in rs) / total:.0f}%)"
    return out


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
           "♻️ reused the helper · ✗reuse didn't · 👁 helper was in the context", "",
           "Output protocol (label stays strict): (fmt →p/t) code present but <file> tags broken; p/t is "
           "what that code scores when extracted leniently · (asked) pretend tool calls, i.e. asked to "
           "explore · (no code) prose only", ""]
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
                        "/".join(str(sum(r.get("format_status") == s for r in rs))
                                 for s in ("bad_format", "no_code", "tool_request")),
                        sum(1 for r in rs if r.get("lenient_total") and r["lenient_passed"] == r["lenient_total"]),
                        f"{sum(bool(r['reuse_passed']) for r in ru)}/{len(ru)}" if ru else "–",
                        sum(r["tokens_in"] for r in rs) // len(rs), _cache_share(rs),
                        sum(r["tokens_out"] for r in rs) // len(rs),
                        f"{peak} ({100 * peak / rs[0]['context_window']:.1f}%)",
                        f"{sum(r['latency_s'] for r in rs) / len(rs):.0f}s",
                        f"{sum(r['tool_calls'] for r in rs) / len(rs):.1f}"])
    out += _table(["Config", "runs", "ok", "broken", "no_fit", "tool_error", "ran out",
                   "fmt/no code/asked", "lenient ok", "reuse ok",
                   "mean in", "cacheable in", "mean out", "max peak", "mean latency", "mean tools"], summary)

    ru = [r for r in rows if r["reuse_passed"] is not None]
    if ru:
        out += ["", "## Reuse vs helper visibility (all configurations)", ""]
        vis = []
        for seen, name in ((True, "helper in context"), (False, "helper not in context")):
            group = [r for r in ru if bool(r["helper_in_context"]) is seen]
            vis.append([name, len(group), sum(bool(r["reuse_passed"]) for r in group),
                        sum(r["label"] == "ok" for r in group)])
        out += _table(["", "reuse runs", "reused", "tests ok"], vis)

    out += ["", "## Performance per model", "",
            "Per request = output tokens / model-call time (thinking included). Combined = all output /"
            " wall time the server was busy, so concurrent requests count once. backend, concurrency and"
            " combined exist only for runs recorded since 2026-11-20 (older rows: unknown / 1 / –).", ""]
    out += performance(rows)

    errors = [r for r in rows if r.get("error")]
    if errors:
        out += ["", "## Errors", ""]
        out += [f"- {r['task_id']} {label(r)}: {r['error']}" for r in sorted(errors, key=lambda r: (r['task_id'], label(r)))]
    return "\n".join(out) + "\n"
