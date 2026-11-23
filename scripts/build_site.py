"""Build the data files for the GitHub Pages site (docs/raw/) from results/ and tasks/.

    python scripts/build_site.py            # writes docs/raw/{index,tasks}.json and docs/raw/runs/<id>.json

Which rows: the final round (thinking on, clean mode) and the thinking-off development round at the
same prompt version (latest row per configuration; old-harness L7 rows are left out). Every text field
goes through `scrub()`, which replaces local absolute paths, and the script ends with a leak scan.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# which round's data: results/ (the current round) or an archive, e.g. results/archive-v3
RESULTS = Path(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--results=")), ROOT / "results"))
if not RESULTS.is_absolute():
    RESULTS = ROOT / RESULTS
# where the site lives: docs/ (published) or another folder, e.g. a preview of the round in progress
SITE = Path(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--out=")), ROOT / "docs"))
if not SITE.is_absolute():
    SITE = ROOT / SITE
OUT = SITE / "raw"
REUSE_HINT = "Prefer reusing existing project helpers over writing new ones."

# ---- privacy -------------------------------------------------------------------------------------
_SCRUB = [
    (re.compile(r"/tmp/cbench-[^/\s]+/repo/"), ""),                 # sandbox copies of the fixture repo
    (re.compile(re.escape(str(ROOT)) + "/"), ""),                  # this checkout
    (re.compile(r"/tmp/cbench-judge[^\s'\"]*"), "<judge-dir>"),
    (re.compile(r"/home/[^/\s]+/"), "~/"),                          # anything else under a home directory
]
_LEAKS = re.compile(r"/home/|/tmp/cbench|localhost:\d+|127\.0\.0\.1|athome|engineeringthefuture|Bearer ")


def scrub(text: str | None) -> str | None:
    if not text:
        return text
    for pat, rep in _SCRUB:
        text = pat.sub(rep, text)
    return text


# ---- which runs ----------------------------------------------------------------------------------
def config_key(r: dict) -> tuple:
    return (r["task_id"], r["level"], r["retriever"], r["query_mode"], r["agent_variant"], r["prompt_arm"],
            r["repo_size"], bool(r.get("map_docs")), bool(r.get("thinking")))   # thinking: the L7 thinking arm


def level_label(r: dict) -> str:
    s = r["level"] + ("+docs" if r.get("map_docs") else "")
    if r["level"] in ("L4", "L5"):
        s += " " + r["retriever"]
    if r["level"] == "L5":
        s += " " + r["query_mode"]
    if r["level"] == "L7":
        s += " " + r["agent_variant"]
    if r["prompt_arm"] == "reuse_hint":
        s += " +hint"
    if r.get("thinking") and not r.get("cold_start"):     # a thinking arm of the thinking-off round
        s += " thinking"
    return s


def load_grades() -> dict[str, dict]:
    """Latest rubric-v2 grade per run (rank and scores from the blind comparison judge)."""
    path = RESULTS / "grades.jsonl"
    out: dict[str, dict] = {}
    if path.exists():
        for line in path.open():
            g = json.loads(line)
            if str(g.get("rubric_version")) == "2":
                out[g["run_id"]] = g
    return out


GRADES: dict[str, dict] = {}
TARGETS: dict[str, str] = {}      # task id -> target file, from tasks/ (v4 prompts don't name it)
PENALTIES: dict[str, dict] = {}
BADGES: dict[str, dict] = {}


def load_badges() -> dict[str, dict]:
    """run_id -> {"exact": [...], "intent": [{badge, confidence, margin, sentence}]} (cbench badges)."""
    path = RESULTS / "badges.jsonl"
    out: dict[str, dict] = {}
    if path.exists():
        for line in path.open():
            if line.strip():
                g = json.loads(line)
                out[g["run_id"]] = g
    return out


def load_penalties() -> dict[str, dict]:
    """Latest penalty grade per run (penalty judge, passing patches only)."""
    path = RESULTS / "penalties.jsonl"
    out: dict[str, dict] = {}
    if path.exists():
        for line in path.open():
            if line.strip():
                g = json.loads(line)
                out[g["run_id"]] = g
    return out


def select_rows() -> list[dict]:
    rows = [json.loads(line) for line in open(RESULTS / "results.jsonl")]
    rows = [r for r in rows if r.get("backend") == "vllm" and r.get("model") == "Qwen/Qwen3.8-27B"]
    pv = max((r.get("prompt_version", 1) for r in rows), default=3)          # the round's prompt version
    final = {config_key(r): r for r in rows if r.get("thinking") and r.get("cold_start")
             and r.get("prompt_version", 1) == pv}
    nothink: dict[tuple, dict] = {}
    for r in rows:
        if (r.get("thinking") and r.get("cold_start")) or r.get("prompt_version", 1) != pv:
            continue                                                 # the final round is taken above
        if r["level"] == "L7" and r.get("failed_call_cost") is None:
            continue                                                 # original harness (" h1")
        nothink[config_key(r)] = r                                   # latest row per configuration
    out = [dict(r, round="final") for r in final.values()] + [dict(r, round="nothink") for r in nothink.values()]
    return [r for r in out if (RESULTS / "runs" / r["run_id"]).is_dir()]


# ---- prompt segmentation -------------------------------------------------------------------------
# Kinds (the viewer colours them): system, hint, task, target, file, retrieved, map, tools, closing.
# (line prefix, kind, label): where a section of the <context> block starts
_SECTIONS = [
    ("Retrieved files", "retrieved", "Retrieval results"),
    ("Retrieved code from other files", "retrieved", "Retrieval results"),
    ("Repository map", "map", "Repository map"),
    ("Repository files and names:", "map", "Repository files and names (L8)"),
    ("Repository files:", "map", "Repository files (L7, prompt v4)"),
    ("You can explore the repository", "tools", "Tool instructions"),
    ("The repository's files", "tools", "Tool instructions"),
]


def segment_system(text: str) -> list[dict]:
    if text.endswith("\n" + REUSE_HINT):
        return [seg("system", "Agent identity and rules", text[: -len(REUSE_HINT) - 1]),
                seg("hint", "Reuse hint (ablation arm)", REUSE_HINT)]
    return [seg("system", "Agent identity and rules", text)]


def seg(kind: str, label: str, text: str) -> dict:
    return {"kind": kind, "label": label, "text": text}


def segment_context(ctx: str, target: str) -> list[dict]:
    """Split the <context> block into file blocks and section headers/bodies."""
    if ctx.strip() == "(none)":
        return [seg("closing", "No context", ctx.strip())]
    segs: list[dict] = []
    section = "file"
    cur: list[str] = []
    cur_kind, cur_label = None, None
    in_fence = False

    def flush():
        nonlocal cur
        text = "\n".join(cur).strip("\n")
        if text:
            segs.append(seg(cur_kind or "file", cur_label or "Context", text))
        cur = []

    for line in ctx.split("\n"):
        if line.startswith("```"):
            in_fence = not in_fence
        if not in_fence:
            hit = next(((p, k, lab) for p, k, lab in _SECTIONS if line.startswith(p)), None)
            if hit and not (hit[1] == section == "tools"):            # one block for the whole tool note
                flush()
                section = hit[1]
                cur_kind, cur_label = section, hit[2]
                cur = [line]
                continue
            if line.startswith("File: ") and not line.startswith("```"):
                path = line[6:].strip()
                flush()
                if path == target:
                    cur_kind, cur_label = "target", f"Target file · {path}"
                elif section == "retrieved":
                    cur_kind, cur_label = "retrieved", f"Retrieved · {path}"
                else:
                    cur_kind, cur_label = "file", f"Repo file · {path}"
                cur = [line]
                continue
        cur.append(line)
    flush()
    return segs


def segment_user(text: str, target: str) -> list[dict]:
    """<context>, <task>, then the closing lines: "Target file: ..." (prompt v1-v3) and the output
    reminder (v3 and later; v4 has no target line)."""
    m = re.match(r"<context>\n(.*)\n</context>\n\n<task>\n(.*)\n</task>(.*)$", text, re.S)
    if not m:
        return [seg("closing", "User message", text)]
    ctx, task, rest = m.groups()
    segs = segment_context(ctx, target)
    segs.append(seg("task", "Task (change request)", task))
    if rest.strip():
        label = "Target and output reminder" if "Target file:" in rest else "Output reminder"
        segs.append(seg("closing", label, rest.strip()))
    return segs


# ---- per-run payload -----------------------------------------------------------------------------
INDEX_FIELDS = ["run_id", "round", "task_id", "category", "tier", "level", "retriever", "query_mode",
                "agent_variant", "prompt_arm", "repo_size", "map_docs", "thinking", "label", "format_status",
                "reuse_passed", "tests_passed", "tests_total", "repo_tests_passed", "repo_tests_total",
                "tokens_in", "tokens_out", "tokens_cached", "latency_s", "tool_calls", "tool_errors",
                "helper_in_context", "helper_body_in_context", "legacy_used", "ctx_fits", "cold_method",
                "concurrency", "started_at", "drops", "drop_calls", "rereads_after_drop",
                "helper_body_at_answer", "dropped_tokens", "drop_saved_tokens", "drop_refunds",
                "drop_refund_clawbacks",
                "patch_stmts", "patch_coverage", "tests_added"]


def outcome(r: dict) -> str:
    """Report outcome (the stored label is unchanged): PASS, FAIL, INVALID (no accepted answer),
    OUT OF TOKENS (hit the output limit), NO FIT (prompt too long). Infrastructure errors are never
    recorded; those runs are redone."""
    if r.get("label") == "no_fit":
        return "NO FIT"
    if "max_output_tokens" in (r.get("error") or ""):
        return "OUT OF TOKENS"
    if r.get("label") == "tool_error" or r.get("format_status", "ok") != "ok":
        return "INVALID"
    return "PASS" if r.get("label") == "ok" else "FAIL"


def read(p: Path) -> str | None:
    return p.read_text(errors="replace") if p.exists() else None


def run_payload(r: dict) -> dict:
    d = RESULTS / "runs" / r["run_id"]
    target = r.get("target_file")
    prompt = json.loads(read(d / "prompt.json") or "[]")
    system = next((m["content"] for m in prompt if m["role"] == "system"), "")
    user = next((m["content"] for m in prompt if m["role"] == "user"), "")
    if not target:
        m = re.search(r"^Target file: (.+)$", user, re.M)
        target = m.group(1).strip() if m else TARGETS.get(r["task_id"], "")   # v4 prompts name no target
    transcript = []
    if (d / "transcript.jsonl").exists():
        for line in open(d / "transcript.jsonl"):
            t = json.loads(line)
            if "message" in t and "role" not in t:                  # a wrapped final message
                t = dict(t["message"], finish_reason=t.get("finish_reason"))
            if "role" not in t:                                      # an L5 model_queries planning step
                transcript.append({"role": "step", "step": t.get("step"),
                                   "instruction": scrub(t.get("instruction")), "reply": scrub(t.get("reply")),
                                   "queries": t.get("queries"), "hits": t.get("hits")})
                continue
            item = {"role": t["role"], "content": scrub(t.get("content") or "")}
            if t.get("reasoning"):                                   # per-turn thinking (L7, from 2026-11-23)
                item["reasoning"] = scrub(t["reasoning"])
            if t.get("tool_calls"):
                item["tool_calls"] = [{"id": c.get("id"), "name": c["function"]["name"],
                                       "arguments": scrub(c["function"].get("arguments", ""))}
                                      for c in t["tool_calls"]]
            if t.get("tool_call_id"):
                item["tool_call_id"] = t["tool_call_id"]
            if t.get("result_ids"):                                  # selective: [#n] segment IDs in this result
                item["result_ids"] = t["result_ids"]
            if t.get("dropped_ids"):                                 # selective: what a drop call removed
                item["drop"] = t["dropped_ids"]
            transcript.append(item)
    meta = {k: r.get(k) for k in INDEX_FIELDS}
    g = GRADES.get(r["run_id"], {})
    meta.update(grade_rank_pct=g.get("rank_pct"), grade_correctness=g.get("correctness"), grade_reuse=g.get("reuse"),
                grade_conventions=g.get("conventions"), grade_scope=g.get("scope"), grade_readability=g.get("readability"),
                grade_candidates=g.get("n_candidates"))
    bd = BADGES.get(r["run_id"], {})
    meta["badges"] = bd.get("exact", []) + [f'{x["badge"]} ({x["confidence"]})' for x in bd.get("intent", [])]
    pen = PENALTIES.get(r["run_id"])
    meta.update(penalty_points=pen["points"] if pen else None, penalty_judge=pen["judge"] if pen else None)
    meta.update(level_label=level_label(r), outcome=outcome(r), model=r.get("model"), quant=r.get("quant"),
                embedder=r.get("embedder"), prompt_version=r.get("prompt_version"),
                max_output_tokens=r.get("max_output_tokens"), context_window=r.get("context_window"),
                peak_ctx_tokens=r.get("peak_ctx_tokens"), target_file=target)
    return {
        "meta": meta,
        "system": [dict(s, text=scrub(s["text"])) for s in segment_system(system)],
        "user": [dict(s, text=scrub(s["text"])) for s in segment_user(user, target)],
        "transcript": transcript,
        "reasoning": scrub(read(d / "reasoning.txt")),
        "response": scrub(read(d / "response.txt")),
        "patch": scrub(read(d / "patch.diff")),
        "tests": scrub(read(d / "tests.txt")),
        "judge_note": scrub(GRADES.get(r["run_id"], {}).get("note")),
        "badges": {"exact": BADGES.get(r["run_id"], {}).get("exact", []),
                   "intent": [dict(x, sentence=scrub(x.get("sentence"))) for x in BADGES.get(r["run_id"], {}).get("intent", [])]},
        "penalties": [{"code": p["code"], "points": p["points"], "quote": scrub(p["quote"]), "why": scrub(p["why"])}
                      for p in PENALTIES.get(r["run_id"], {}).get("penalties", [])],
    }


# ---- tasks ---------------------------------------------------------------------------------------
def tasks_payload() -> list[dict]:
    reg = tomllib.loads((ROOT / "helpers" / "registry.toml").read_text())
    naming = {h["qualname"]: h.get("naming") for h in reg.get("helper", [])}
    decoys = reg.get("decoy", [])
    out = []
    for td in sorted((ROOT / "tasks").iterdir()):
        if not (td / "task.toml").exists():
            continue
        t = tomllib.loads((td / "task.toml").read_text())
        helpers = t.get("needed_helpers", [])
        out.append({
            "id": t["id"], "category": t["category"], "tier": t.get("tier"), "target_file": t["target_file"],
            "repo_sizes": t.get("repo_sizes", ["S", "M"]), "prompt": t["prompt"].strip(),
            "helpers": [{"qualname": h, "naming": naming.get(h)} for h in helpers],
            "decoys": [d for d in decoys if d.get("looks_like") in helpers],
            "hidden_tests": {p.name: scrub(p.read_text()) for p in sorted((td / "hidden_tests").glob("*.py"))},
            "solutions": {p.stem: scrub(p.read_text()) for p in sorted((td / "solutions").glob("*.diff"))},
        })
    return out


# ---- main ----------------------------------------------------------------------------------------
def main() -> int:
    GRADES.update(load_grades())
    for td in sorted((ROOT / "tasks").iterdir()):
        if (td / "task.toml").is_file():
            TARGETS[td.name] = tomllib.loads((td / "task.toml").read_text()).get("target_file", "")
    PENALTIES.update(load_penalties())
    BADGES.update(load_badges())
    rows = select_rows()
    if (OUT / "runs").exists():
        shutil.rmtree(OUT / "runs")
    (OUT / "runs").mkdir(parents=True)
    index = []
    for r in rows:
        p = run_payload(r)
        (OUT / "runs" / f"{r['run_id']}.json").write_text(json.dumps(p, separators=(",", ":")))
        index.append(p["meta"])
    index.sort(key=lambda m: (m["task_id"], m["level_label"], m["repo_size"], m["round"]))
    (OUT / "index.json").write_text(json.dumps(index, separators=(",", ":")))
    (OUT / "tasks.json").write_text(json.dumps(tasks_payload(), separators=(",", ":")))

    leaks = []
    for f in OUT.rglob("*.json"):
        for m in _LEAKS.finditer(f.read_text()):
            leaks.append((f.name, m.group(0)))
    size = sum(f.stat().st_size for f in OUT.rglob("*.json"))
    rounds = {k: sum(m["round"] == k for m in index) for k in ("final", "nothink")}
    print(f"{len(index)} runs ({rounds}), {size / 1e6:.1f} MB in {OUT}")
    if leaks:
        print(f"possible leaks: {len(leaks)}; first: {leaks[:5]}")
        return 1
    print("leak scan clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
