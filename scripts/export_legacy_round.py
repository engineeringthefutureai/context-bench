"""Export an archived round's raw run bundles as plain files under docs/raw/, so individual runs can be
linked and read on GitHub (a patch.diff opens as a diff, not as an escaped JSON field).

    python scripts/export_legacy_round.py                       # results/archive-v3 -> docs/raw/legacy_v3/

Writes README.md (what the round was), index.csv (one row per run), results.jsonl, grades.jsonl and
runs/<run_id>/ with the bundle's text files. Every text goes through build_site.scrub() (local paths) and the
export ends with build_site's leak scan. The site's pages and viewer don't read this folder.
"""
from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
from build_site import _LEAKS, config_key, level_label, outcome, scrub  # noqa: E402

SRC = ROOT / "results" / "archive-v3"
OUT = ROOT / "docs" / "raw" / "legacy_v3"
FILES = ("patch.diff", "response.txt", "reasoning.txt", "tests.txt", "prompt.json", "transcript.jsonl", "run.json")

README = """# Legacy round v3 (prompt v3, 21–23 November 2026)

The raw runs of the round before the published one (v4). Kept so individual runs can be linked and read;
the site's Results, Data and Cases pages show the v4 round only. Snapshot tag: `round-v3`.

- **Model:** Qwen 3.8 27B (FP8, vLLM), one shot per configuration (seed 1).
- **Repos:** S and M (repo L came with v4). **Levels:** L0–L7 as they were then; L8 and L-AG didn't exist.
- **Thinking:** off (development) and on (the round's final pass). **Prompt v3:** every level, L7 included,
  was given the target file (v4 dropped it from the shared prompt).
- **Runs:** {n} ({by_round}). {sup} of them are L7 runs from the original agent harness (no feedback on a
  failed tool call; a tool call written as text ended the run), rerun after the harness changed; `index.csv`
  marks them `superseded`. The round itself is the other {kept} (the site counted 440 + 440).

## What is where

- `index.csv`: one row per run: run id, task, repo, level as the site names it, thinking, outcome, hidden
  tests, the repo's original tests broken by the patch, and the folder.
- `runs/<run_id>/`: `patch.diff` (the change as applied), `response.txt` (the model's reply), `reasoning.txt`
  (thinking, when on), `tests.txt` (hidden-test output, and the repo's own tests where they were run),
  `prompt.json` (the messages sent), `transcript.jsonl` (L7 tool calls and side exchanges), `run.json`.
- `results.jsonl`: the result rows; `grades.jsonl`: the blind judge's grades (Claude Code judge; rubric v1,
  and v2 from 22 November; never compare grades across rubric versions).

## Outcomes

Outcomes are named as on the site (PASS, FAIL, UNPARSABLE, ASKED TO EXPLORE, ...). **BROKE KNOWN TESTS** was
added on 26 November 2026 and applied to this round retroactively: the hidden tests passed, but at least one
of the repository's original tests no longer passes after the patch (it fails, its module no longer imports,
or it is gone). Those runs were PASS when the round was recorded. `known_tests_broken` in `results.jsonl`
names the tests. {breaks} runs of this round have that outcome, none of them superseded.

Paths on the machine that ran the round were replaced (`~/`, sandbox prefixes removed).
"""


def scrub_json_text(text: str) -> str:
    return scrub(text) or ""


def main() -> int:
    rows = [json.loads(line) for line in open(SRC / "results.jsonl")]
    if OUT.exists():
        shutil.rmtree(OUT)
    (OUT / "runs").mkdir(parents=True)
    index = []
    per_config = {}
    for r in rows:
        per_config.setdefault(config_key(r), []).append(r)
    superseded = {r["run_id"] for rs in per_config.values() if len(rs) > 1 for r in rs
                  if r["level"] == "L7" and r.get("failed_call_cost") is None}
    for r in rows:
        src = SRC / "runs" / r["run_id"]
        dst = OUT / "runs" / r["run_id"]
        dst.mkdir()
        for name in FILES:
            if (src / name).is_file():
                (dst / name).write_text(scrub_json_text((src / name).read_text()))
        broken = r.get("known_tests_broken") or []
        index.append({"run_id": r["run_id"], "task": r["task_id"], "repo": r["repo_size"], "level": level_label(r),
                      "thinking": "on" if r.get("thinking") else "off", "outcome": outcome(r),
                      "hidden_tests": f"{r['tests_passed']}/{r['tests_total']}",
                      "known_tests_broken": f"{len(broken)}/{r['known_tests_total']}" if r.get("known_tests_total") else "",
                      "superseded": "yes" if r["run_id"] in superseded else "",
                      "folder": f"runs/{r['run_id']}/"})
    with open(OUT / "index.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(index[0]))
        w.writeheader()
        w.writerows(sorted(index, key=lambda x: (x["task"], x["repo"], x["level"], x["thinking"])))
    (OUT / "results.jsonl").write_text("".join(scrub_json_text(json.dumps(r)) + "\n" for r in rows))
    grades = SRC / "grades.jsonl"
    if grades.is_file():
        (OUT / "grades.jsonl").write_text(scrub_json_text(grades.read_text()))
    on = sum(bool(r.get("thinking")) for r in rows)
    (OUT / "README.md").write_text(README.format(
        n=len(rows), by_round=f"{len(rows) - on} thinking off, {on} thinking on",
        sup=len(superseded), kept=len(rows) - len(superseded),
        breaks=sum(r["label"] == "breaks_tests" for r in rows)))
    leaks = [p for p in OUT.rglob("*") if p.is_file() and _LEAKS.search(p.read_text(errors="replace"))]
    if leaks:
        print("LEAK SCAN FAILED:", *leaks[:20], sep="\n  ")
        return 1
    print(f"exported {len(rows)} runs to {OUT.relative_to(ROOT)}; leak scan clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
