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

## Implementation (done 2026-11-23; no run without the user's go)
- `context_bench/penalty.py` (its own module; the comparison judge in `grade.py` stays for the recorded
  rounds), `grading/penalties.md` (catalogue v1; the judge sees the codes, not the points, so it can't
  aim at a score).
- `cbench grade --mode penalty --judge agy|claude [--sample N] [--task ...] [--repo-size ...] [--dry-run]`;
  judges in `[grading.judges]`. Identical patches are judged once; a patch already graded by the same judge
  and catalogue version is reused for new runs, so later passes only grade new patches.
- `results/penalties.jsonl`: per run, judge, catalogue version, accepted penalties (code, points, quote,
  why), dropped penalties with the reason, points.
- `cbench report` adds a penalty section (hard pass, graded, mean points, share with no penalty, top codes);
  the viewer shows points and each penalty under Tests. The Results page section comes with the first data.
- Tests: `tests/test_penalty.py` (validation, caps, the judge dir, resume and reuse, a fake judge).

## One conversation per task, in Antigravity (2026-11-24)
A calibration pick of five patches (all five verdicts as expected, nothing dropped) used about 15% of the
5-hour agy quota. agy bills quota by token cost, and a fresh headless call per patch makes the judge
explore the repository again every time, which is most of those tokens. 485 thinking-off patches would
have needed about twice the weekly quota.

`scripts/agy_grader.py` grades the same patches in one Antigravity conversation per task and repo (57
groups, 1-17 patches each). agy runs the script (or Antigravity starts it as a sidecar), so it inherits
the environment `agentapi` needs, and the graders are ordinary conversations in the app, where they can be
watched and steered:
- `cbench grade --mode penalty --judge agy --thinking off --export grading/queue/v4-off` writes the
  queue (patches by hash, task texts, the catalogue; no run ids, nothing about how a patch was made). It
  goes to the Antigravity machine through the repository.
- The script copies the fixture into a judge dir under `.cache/cbench-judge` (gitignored, inside the
  workspace, so agy doesn't ask about every step) and starts a conversation
  (`agentapi new-conversation`). Each patch is one message: `patch.diff` is rewritten first, so only the
  current change is ever in the dir, and the grader writes its JSON to `verdicts/<id>.json` there, which
  the script waits for. Later patches go to the same conversation (`agentapi send-message`) and ask for the
  new change to be graded on its own. A fresh conversation starts every 10 patches.
- No verdict within 15 minutes, or one without the JSON, gets a neutral message ("write the JSON for
  patch.diff to <file>"). After two, the patch is parked with its conversation id; the conversation stays
  open in the app. Messages never mention a verdict, and each grade records the ones it took.
- The script waits while the 5-hour or weekly quota is below 10%.
- `cbench grade --mode penalty --judge agy --import <queue>/results.jsonl` runs the usual quote check and
  writes `results/penalties.jsonl`, with the conversation id and messages on each grade.

The graders run with the app's permissions rather than a read-only allow-list, and the judge dir sits inside the
checkout, so blindness rests on the prompt (only the judge dir belongs to the job); the app shows every
step for review, and a grader that opens `docs/` or `results/` should have its grade discarded.
The trade-off: a judge that has seen earlier changes for the same task may grade the next one relative
to them. The catalogue names absolute defects, which limits this. The five calibration patches, graded
again in one conversation, show whether it matters.
