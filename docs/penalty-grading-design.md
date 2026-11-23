# Penalty grading (design, agreed 2026-11-23)

Replaces the comparison-based judge (`cbench grade`, panels ranked best-first, rubric v1/v2) for the
next round. Nothing is graded without asking the user first, including which judge to use.

## Why
The current judge ranks all candidate patches of a task against each other. Its ranks mean nothing
outside their panel, so rounds and judges can't be compared and a judge change forces a full regrade.
In the v3 round it mostly repeated the tests: 114/114 failing runs scored low, 307/322 passing runs scored
high. Its one distinctive signal was 15 passing runs it scored low. Large panels also broke agy (loops
over candidate files were denied).

## Two scores per run
1. **Hard score**, computed by the harness from objective checks only, never changed by a judge:
   - hidden tests pass (the label)
   - reuse confirmed by the spy test (reuse tasks; n/a when the reuse test couldn't run)
   - the repo's own tests kept green
   - patch coverage (share of changed statements the hidden tests ran)
   Reported as the separate parts, plus a combined pass/fail (hidden tests pass and repo tests kept).
2. **Hard score with the AI adjustment**: penalty points from the judge, applied to passing runs. Both
   numbers are reported side by side, so a reader can see how much the judge moves anything.

## Penalty catalogue (the judge may only cite these)

| Code | Defect | Points |
|---|---|---|
| `scope` | changes unrelated to the task | -10 |
| `reimplemented` | re-implements logic the repository already has (beyond what the reuse test checks) | -10 |
| `convention` | breaks a convention visible in the repo (float money, error handling, events, settings) | -5 each, max -10 |
| `api` | changes a public signature or behaviour other code relies on | -15 |
| `wrong_untested` | logic that looks wrong in a place no test exercises | -15 |
| `risk` | security or data problem (personal data in logs, unvalidated input, secrets) | -20 |

The total is capped at -40. Dead-code use (`legacy_used`) and reuse are measured exactly by the harness,
so they are not in the catalogue.

## Judge protocol
- **One patch per call**: the task text, the patch, the repository before the change, the catalogue, and
  whether the hidden tests passed. It never learns how the patch was made (level, model, prompt, tools).
- **Reply**: JSON `{"penalties": [{"code": ..., "quote": "<a line from the diff>", "why": "<one sentence>"}]}`;
  an empty list is a valid answer.
- **Validation by the harness**: unknown codes are dropped; a penalty whose quote doesn't appear in the
  diff's added or removed lines is dropped (catches hallucinated defects); points are summed and capped.
  Dropped penalties are counted per judge (a judge-quality signal).
- **Scope**: passing patches only (failing ones get nothing the tests don't already say). Optionally a
  sample of failing ones to calibrate.
- **Read-only judge**, as now, in a directory outside the project; the patch is one file, so no loops are
  needed.
- **Recorded per run**: judge name, catalogue version, each accepted penalty (code, points, quote, why),
  dropped penalties, total. Grades are absolute, so a new round grades only its new patches.

## Choosing the judge
Before a full pass: agy and Claude Code on the same ~30 passing patches (including the 15 that the v3
judge scored low), compare penalties, and read a sample by hand. Pick one judge per round; don't mix
penalties from different judges in one comparison.

## Report
Results table: hard-score parts per configuration, the pass rate with and without the AI adjustment, mean
penalty points per passing run, and the most frequent penalty codes with example quotes linked to the
viewer. The limits section says what the judge is and that its penalties are unverified unless sampled.

## Implementation (before the v4 rerun finishes; no run without the user's go)
- `context_bench/grade.py`: a `penalty` mode next to the comparison mode (kept for the recorded rounds).
- `grading/penalties.md`: the catalogue and instructions (versioned like the rubric).
- `cbench grade --mode penalty [--judge agy|claude] [--sample N]`.
- `results/penalties.jsonl`; `scripts/build_site.py` and `scripts/build_report.py` read it.
- Tests: validation (unknown code, quote not in diff, cap), the judge never sees run metadata.
