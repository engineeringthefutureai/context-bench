"""Penalty grading (cbench grade --mode penalty; design: docs/penalty-grading-design.md).

One judge call per distinct passing patch. The judge directory, outside the project tree, holds only:
    repo/          the fixture repo before the change
    task.md        the task text as the models saw it
    patch.diff     the change
    PENALTIES.md   grading/penalties.md (the catalogue and the reply format; points are not shown)
Nothing about how the patch was made (level, model, prompt, context, tools) goes in.

The judge replies {"penalties": [{"code", "quote", "why"}]}. The harness keeps a penalty only when its
code is in the catalogue and its quote appears in an added or removed line of the diff; anything else is
recorded as dropped (a judge-quality signal). Points are summed (convention at most -10) and capped at
-40. Grades are absolute: a patch already graded by the same judge and catalogue version is skipped, so a
new round grades only its new patches. One line per run in results/penalties.jsonl.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

POINTS = {"scope": -10, "reimplemented": -10, "convention": -5, "api": -15, "wrong_untested": -15, "risk": -20}
CODE_CAP = {"convention": -10}
TOTAL_CAP = -40
JUDGE_PROMPT = ("Follow PENALTIES.md in the current directory: read task.md and patch.diff, look at repo/ "
                "as needed, and reply with the JSON it asks for, nothing else.")


def patch_hash(patch: str) -> str:
    return hashlib.sha256(patch.encode()).hexdigest()


def changed_lines(patch: str) -> list[str]:
    """Added and removed lines of a unified diff, without the +/- and surrounding whitespace."""
    return [line[1:].strip() for line in patch.splitlines()
            if line[:1] in "+-" and not line.startswith(("+++", "---")) and line[1:].strip()]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().lstrip("+-").strip())


def validate(raw: list, patch: str) -> tuple[list[dict], list[dict], int]:
    """(accepted, dropped, points). Accepted: known code, quote found in a changed line (whitespace
    normalised; a quote of at least 4 characters may be part of a line), first time for that code and
    line. Points per code are capped (CODE_CAP), the total at TOTAL_CAP."""
    lines = [_norm(l) for l in changed_lines(patch)]
    accepted, dropped, seen = [], [], set()
    for p in raw if isinstance(raw, list) else []:
        if not isinstance(p, dict):
            dropped.append({"penalty": p, "reason": "not an object"})
            continue
        code, quote = str(p.get("code", "")).strip(), _norm(str(p.get("quote", "")))
        if code not in POINTS:
            dropped.append(dict(p, reason="unknown code"))
            continue
        hit = next((l for l in lines if quote and (quote == l or (len(quote) >= 4 and quote in l))), None)
        if hit is None:
            dropped.append(dict(p, reason="quote not in the diff"))
            continue
        if (code, hit) in seen:
            dropped.append(dict(p, reason="duplicate"))
            continue
        seen.add((code, hit))
        accepted.append({"code": code, "points": POINTS[code], "quote": p.get("quote", ""),
                         "why": str(p.get("why", ""))[:500]})
    by_code: dict[str, int] = {}
    for a in accepted:
        by_code[a["code"]] = by_code.get(a["code"], 0) + a["points"]
    points = sum(max(v, CODE_CAP.get(c, v)) for c, v in by_code.items())
    return accepted, dropped, max(points, TOTAL_CAP)


def parse_reply(text: str) -> list:
    """The judge's penalty list (a ```json fence is tolerated). Raises ValueError on anything else."""
    m = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.S)
    data = json.loads(m.group(1) if m else text.strip())
    if not isinstance(data, dict) or not isinstance(data.get("penalties"), list):
        raise ValueError("reply has no penalties list")
    return data["penalties"]


def write_judge_dir(task_prompt: str, repo: Path, patch: str, catalogue: Path, dst: Path) -> Path:
    """Remove dst if it exists, then write the layout above."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(repo, dst / "repo", ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    (dst / "task.md").write_text(task_prompt.strip() + "\n")
    (dst / "patch.diff").write_text(patch)
    shutil.copyfile(catalogue, dst / "PENALTIES.md")
    return dst


def run_judge(judge_dir: Path, judge_command: list[str], timeout: int = 1200) -> list:
    """Run the judge in judge_dir; its stdout is the CLI's JSON envelope (`result` for Claude Code,
    `response` for agy). Retry once on an invalid reply, then raise."""
    error = None
    for _ in range(2):
        proc = subprocess.run(judge_command + [JUDGE_PROMPT], cwd=judge_dir, capture_output=True, text=True,
                              timeout=timeout)
        try:
            envelope = json.loads(proc.stdout)
            text = (envelope.get("result", envelope.get("response")) if isinstance(envelope, dict)
                    else proc.stdout)
            return parse_reply(text)
        except (ValueError, KeyError, TypeError) as e:
            error = f"{type(e).__name__}: {e} (exit {proc.returncode}; stderr: {proc.stderr[-300:]})"
    raise RuntimeError(f"judge gave no valid reply twice: {error}")


def load(path: Path) -> dict[str, dict]:
    """Latest penalty grade per run_id."""
    out = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.strip():
                g = json.loads(line)
                out[g["run_id"]] = g
    return out


def pending(rows: list[dict], runs_dir: Path, done: dict[str, dict], judge: str, version: str,
            sample: int | None = None) -> dict[str, dict]:
    """Distinct passing patches still to grade by this judge and catalogue version:
    {patch_hash: {"patch", "rows"}}. A patch graded before is reused for new runs with the same patch
    (they get its grade without a judge call). sample: at most this many patches, chosen at random
    (seed 1), for calibration."""
    graded = {g["patch_hash"]: g for g in done.values() if g["judge"] == judge and g["catalogue_version"] == version}
    out: dict[str, dict] = {}
    for r in rows:
        f = runs_dir / r["run_id"] / "patch.diff"
        if r.get("label") != "ok" or not f.is_file():
            continue
        patch = f.read_text()
        h = patch_hash(patch)
        if (g := done.get(r["run_id"])) and g["judge"] == judge and g["catalogue_version"] == version:
            continue
        out.setdefault(h, {"patch": patch, "rows": [], "reuse": graded.get(h)})["rows"].append(r)
    if sample is not None:
        keys = sorted(out)
        random.Random(1).shuffle(keys)
        out = {k: out[k] for k in sorted(keys[:sample])}
    return out


def grade_patch(task, size: str, item: dict, *, repo: Path, catalogue: Path, version: str, judge: str,
                judge_command: list[str], work_dir: Path, out_path: Path, dry_run: bool = False) -> int:
    """Grade one distinct patch (or reuse its earlier grade) and append one line per run. Returns the
    number of runs graded (0 on dry run)."""
    h = patch_hash(item["patch"])
    if item.get("reuse"):
        prev = item["reuse"]
        accepted, dropped, points = prev["penalties"], prev["dropped"], prev["points"]
    else:
        dst = write_judge_dir(task.prompt, repo, item["patch"], catalogue, work_dir / f"{task.id}@{size}-{h[:12]}")
        if dry_run:
            return 0
        accepted, dropped, points = validate(run_judge(dst, judge_command), item["patch"])
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("a") as f:
        for r in item["rows"]:
            f.write(json.dumps({"run_id": r["run_id"], "task_id": task.id, "repo_size": size, "patch_hash": h,
                                "judge": judge, "catalogue_version": version, "penalties": accepted,
                                "dropped": dropped, "points": points, "graded_at": now}) + "\n")
    return len(item["rows"])


def hard_pass(r: dict) -> bool:
    """Hidden tests pass and the repo's own tests stay green (when recorded)."""
    kept = r.get("repo_tests_total") in (None, 0) or r.get("repo_tests_passed") == r.get("repo_tests_total")
    return r.get("label") == "ok" and kept


def report(rows: list[dict], path: Path) -> list[str]:
    """Markdown lines per configuration: hard pass rate, passing runs graded, mean penalty points per
    graded passing run, share of them with no penalty, and the most frequent codes. Empty when nothing
    is graded."""
    from .results import _table, config_label
    grades = load(path)
    if not any(r["run_id"] in grades for r in rows):
        return []
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(config_label(r), []).append(r)
    judges = sorted({g["judge"] for r in rows if (g := grades.get(r["run_id"]))})
    table = []
    for label, rs in sorted(groups.items()):
        gs = [grades[r["run_id"]] for r in rs if r["run_id"] in grades and r.get("label") == "ok"]
        codes: dict[str, int] = {}
        for g in gs:
            for p in g["penalties"]:
                codes[p["code"]] = codes.get(p["code"], 0) + 1
        top = ", ".join(f"{c} {n}" for c, n in sorted(codes.items(), key=lambda kv: -kv[1])[:3])
        table.append([label, f"{sum(map(hard_pass, rs))}/{len(rs)}", len(gs),
                      f"{sum(g['points'] for g in gs) / len(gs):.1f}" if gs else "-",
                      f"{sum(not g['penalties'] for g in gs) / len(gs):.0%}" if gs else "-", top or "-"])
    note = [f"Penalty judge: {', '.join(judges)}"
            + (". Penalties from different judges are not comparable." if len(judges) > 1 else "."), ""]
    return note + _table(["Config", "hard pass", "graded", "mean points", "no penalty", "top codes"], table)
