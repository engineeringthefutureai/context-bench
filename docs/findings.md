# What we have found so far

*Status 2026-11-21. Development round: thinking off, one run per configuration, 20 of 40 planned
tasks. The final round (thinking on, clean cold server for every run) has not run yet, so treat
every number here as a strong signal, not a result.*

Illustrated version with charts and a hypothesis review: [report.html](report.html).

## Setup in one paragraph

One model, Qwen 3.8 27B (FP8 on vLLM, tensor-parallel over two Intel Arc Pro B60), gets the same
coding tasks with eight different ways of building its context, L0-L7
([diagram](diagrams/context-levels.html)). Only the `<context>` block changes; the prompt is a
generalist "senior engineer" brief that never mentions reuse. Tasks run against `shopkit`, a
synthetic Python shop in two sizes: S (~2k tokens) and M (~41k tokens, with 25 planted helpers,
near-miss decoys and unlabelled dead code). Each run is scored by hidden tests outside the repo, a
spy test that checks the existing helper was actually called, and what the model saw. Latest full
cycle: 386 runs, 20 tasks, both repos.

| Level | Context | Passed |
|---|---|---|
| L0 | task text only (control tasks) | 0/2 |
| L1 | the file being edited | 25% |
| L2 | + its direct imports and its test file | 35% |
| L3 | the whole repository | **78%** |
| L4 | + top-3 files by search (BM25 / embeddings) | 63% / 48% |
| L5 | + top-8 functions by search (best variant: embeddings + model-written queries) | 48-67% |
| L6 | + every signature, no bodies (with / without first docstring line) | 11% / 11% |
| L7 | agent with read-only tools (grep / grep + semantic search) | **78%** / 70% (rerun: 67% / 67%) |

## Findings

### 1. Reuse follows visibility of the helper's code

| What the model saw of the needed helper | Reuse runs | Reused | Passed |
|---|---|---|---|
| its full source | 110 | 94 (85%) | 89 |
| only its signature | 26 | 5 (19%) | 3 |
| only a call site (imported and used elsewhere) | 12 | 8 (67%) | 8 |
| nothing | 41 | 1 (2%) | 7 |

Models don't ignore helpers they can see; they miss helpers they can't. Every context strategy is
therefore mostly a question of whether it gets the helper's *body* in front of the model. A call
site is nearly as good as the definition: the model copies the import (first seen at L4, where a
retrieved invoice renderer that used `format_money` was enough).

### 2. Whole repository and agent tie at the top; the agent is the one that scales

On repo M, L3 passes 85% and L7 78%, but L3 sends ~42k input tokens per run, all of the repo, every
time. L7 sends ~48k in total over its turns, of which only ~11k is fresh: 77% repeats an earlier
turn's prompt and can be billed at a cached rate. L3 only works while the repo fits the window; L7
doesn't care how big the repo is.

| Category | L1 | L2 | L3 | L4 | L5 | L6 | L7 |
|---|---|---|---|---|---|---|---|
| Reuse | 15% | 50% | 92% | 67% | 78% | 13% | 83% |
| Convention (pattern lives in other files) | 0/6 | 0/3 | 2/4 | 2/8 | 8/20 | 1/8 | **7/8** |
| Cross-file (coordinated edits) | 0/6 | 0/3 | 4/4 | 2/8 | 7/20 | 0/8 | **7/8** |

The agent is the only level that is strong on conventions: it finds how the shop raises errors,
publishes events or loads settings by reading around, which no single retrieval query asks for.

### 3. Who writes the search query matters more than the search engine

With the task text as the query, BM25 and embeddings find helpers whose names match the task
wording (5/5 at rank 1) and miss opaque ones (BM25 1/7, embeddings 3/7 in the top 8 on repo M). "Product
codes must be cleaned up the same way as everywhere else" doesn't look like `normalize_sku`, by
words or by meaning. When the model writes its own queries ("clean product code SKU
normalization"), the same embedder ranks `normalize_sku` first. One planning call (L5
model_queries) lifts L5 with embeddings from 52% to 67% at ~3k input tokens.

### 4. Search surfaces decoys; the model can only reject them if it can check

Five patches imported dead decoys (`sku_code`, `cleanSKU`, `slugify`, `kgToLbs` twice) and all
failed. Every one came from a single-shot context built by embedding retrieval or the L6 map. None
came from L3, which shows the dead code's only importers are its own old tests, or from L7, whose
`list_files` shows import counts and whose `find_references` shows usages: the agent opened a dead
file a few times and moved on. Showing a decoy is not a retrieval failure; deciding without a way to
verify is.

### 5. Signatures without bodies backfire

L6 shows every function signature in the repo, which should make helpers easy to spot. It passes
11%. The model does spot the helper, then announces it must read it first ("I need to understand how
the shop rounds percentage amounts. Let me look at the relevant files.") and stops, or writes a
pretend tool call: 23 of 27 L6 failures look like that. The map provokes exploration it gives no
way to do. On the trap task (in a thinking-on sample), the docstring variant trusted the lying
docstring after the least deliberation of any level: 1.6k thinking tokens, against 6.2k for L3,
which caught the drift.

### 6. Documentation drift is caught only by reading and checking

trap-001: `format_weight(grams, unit)` documents a pound option its body no longer implements.
Correct is to reuse it and fix it. Single-shot levels with thinking off either trust it (2/4, with
reuse) or write their own conversion (L2: passes, no reuse). The fix happened at L7 (both variants
in an earlier sample, grep in the latest) and, in an earlier thinking-on sample, at L3. Seeing the
body is necessary; questioning it takes either reasoning or tools.

### 7. Thinking off breaks single-shot runs on protocol; a tool loop makes up for it

With thinking off, the model behaves as if it were in an agent harness: it opens with "I'll explore
the codebase..." and writes tool calls that nobody executes. Earlier, on L0-L3, thinking off passed
9/31 vs 20/31 with thinking on, mostly through broken output, not worse code. In L7, where the tool
calls are real, thinking off and on tied (11/13 each); thinking off used the full 15-call budget
and ~2x the input, thinking on wrote ~7x the output. This matches the common advice to turn
thinking off for coding agents: it holds where a real tool loop exists.

### 8. The prompt matters, and a generalist one is enough

Three system prompts on the same 74 configurations (thinking off): the original 18 passed, a
detailed one with capabilities and output rules 23, a generalist senior-engineer brief (duties,
not benchmark hints) 27, with replies lacking code or asking for tools falling from 36 to 7. No
instruction in it mentions reuse, helpers, docstrings or dead code.

### 9. Cost and speed (indicative)

| Repo M | Passed | Input / run | Fresh input / run | Output / run | Latency |
|---|---|---|---|---|---|
| L1 | 12% | 1.3k | 1.3k | 0.9k | 31 s |
| L2 | 46% | 2.3k | 2.3k | 1.4k | 60 s |
| L3 | 85% | 41.7k | 41.7k | 0.7k | 29 s |
| L4 | 55% | 2.3k | 2.3k | 0.7k | 29 s |
| L5 | 53% | 2.8k | 2.8k | 0.5k | 21 s |
| L6 | 12% | 10.6k | 10.6k | 0.5k | 59 s |
| L7 | 78% | 47.9k | 11.0k | 0.9k | 102 s |

On this server L3 is fast because every task shares the same 40k-token repo prefix: 90% of its input
came from vLLM's prefix cache. Throughput: 16.5 output tok/s per request, 65 combined at 4
concurrent requests; speculative decoding (2 draft tokens) accepts 91% / 83%. The embedder (0.6B
on the integrated GPU) adds 0.06-0.65 s per run once the repo is indexed (75 s for repo M, once).
These timings come from a warm server that ended the run in swap; the final round's clean cold
runs are the ones to quote.

### 10. L7 rerun with failed-call feedback

The tool harness was changed so that a failed tool call (wrong arguments, unknown tool, or a tool
call written as text that vLLM couldn't parse) gets generic feedback and costs half a call of the
15-call budget, instead of a whole call or ending the run. All 54 L7 runs were rerun with it. No
"tool error" failures remain. The three runs that had failed that way all passed. No run produced
an unparsable text tool call this time, so the "couldn't parse" feedback was never needed.

Overall, L7 passed 36/54 (67%), against 40/54 (74%) in the first sample: grep 18/27 (from 21), hybrid
18/27 (from 19). 20 of 54 runs flipped between the two samples in both directions, which is noise of
the size noted below rather than an effect of the harness. Reuse held at 20/24. Cross-file fell
from 7/8 to 4/8, convention from 7/8 to 6/8, and both variants fixed the trap this time. The
L3-vs-L7 tie from finding 2 is better read as "L3 and L7 lead, the gap between them is within noise".

### 11. Regression check: the repo's own tests after each patch

Since the judge found a passing patch that deleted code, every run now also runs the repo's own test
suite (105 tests on repo M, 0.1 s) on the patched sandbox. It is reported apart from the label, like
reuse, and was backfilled for all saved runs. Every task's reference solution keeps it green.

Of 181 passing runs, 179 kept the repo's own tests green. The two that didn't:

- cross-002, L5 embeddings + model queries: the patch the judge flagged, which deleted `pick_list`,
  `packing_slip` and two serializers (104/105).
- trap-001, L7 hybrid (rerun): it fixed `format_weight` correctly, but along the way changed
  `pick_box` to return the box's name instead of the box, breaking its callers, and left
  "-- wait," inside the docstring (104/105). The judge hasn't seen this run yet.

Across all patches, including failing ones, the share that kept the repo's tests green ranges from
71% (L6) and 81% (L1) to 96% (L3) and 100% (L2), with L4 93%, L5 88% and L7 90%.

## How sure we are

- **One sample per configuration.** A full rerun flipped 31 of 152 runs between pass and fail:
  a configuration's count moves by about +-3 of 13 by chance, partly because vLLM batching makes
  even seeded runs nondeterministic. The ladder (L3/L7 > search levels > L6, L1, L0) held in both
  samples; differences between search variants do not yet.
- **Thinking off.** Chosen for fast development; the final round runs with thinking on.
- **20 of 40 tasks, one trap.** Reuse 10, cross-file 3, convention 3, control 3, trap 1.
- **One model, one synthetic repo.** No second model yet; shopkit is purpose-built so the model
  can't have memorised it.
- **One judge.** Code quality comes from one blind Claude Code judge per task (below). The 6
  cases where it disagrees with the tests were checked by hand; the rest were not.

## Judged code quality

All 308 current graded runs (L7 from its rerun; regraded 2026-11-21 after the rerun, since ranks are
relative within a task): per task and repo, one blind Claude Code judge ranked every distinct patch
side by side and scored it 1-5 on correctness, reuse, conventions, scope and readability, without
knowing how any patch was made. Grades never change test labels.

**The judge agrees with the tests.** Patches that passed rank in the best fifth on average (rank
percentile 0.20, correctness 4.8/5); failing ones rank near the bottom (0.71, 1.6/5). Its reuse score
is 4.7 where the reuse spy passed and 1.7 where it failed. It disagrees on 6 of 308, and each time
it has a point:

- Five patches passed the tests but were poor code: one deleted `pick_list`, `packing_slip` and two
  serializers and left `# lines 11-19` placeholders (cross-002, L5). One rewrote `Stock` with a lock
  and a global decimal context (convention-001, L4). One hard-coded `$` instead of reusing
  `format_money` (reuse-001, L4). One replaced a dataclass's fields with a hand-written `__init__`
  while keeping `@dataclass`, so every two orders compare equal (control-002, L7 hybrid, repo S, whose
  2 own tests don't notice). One fixed `format_weight` but changed `pick_box` to
  return the box's name (trap-001, L7 hybrid). The hidden tests don't catch destructive or
  out-of-scope edits. The regression check (finding 11) now catches two of these five, the judge
  all five.
- One failed on a single wrong edge case in otherwise good code (control-003, L5).

**Among passing patches, how the context was built still shows.** Rank percentile among the patches
for the same task (0 = best):

| Level | Graded | All patches | Passing only | Conventions | Readability |
|---|---|---|---|---|---|
| L1 | 27 | 0.67 | 0.46 | 3.3 | 3.7 |
| L2 | 14 | 0.53 | 0.33 | 4.0 | 4.1 |
| L3 | 25 | 0.23 | **0.13** | **4.6** | **4.6** |
| L4 | 46 | 0.33 | 0.21 | 4.2 | 4.3 |
| L5 | 123 | 0.41 | 0.22 | 4.2 | 4.3 |
| L6 | 21 | 0.57 | 0.18 | 4.3 | 4.5 |
| L7 | 52 | 0.34 | 0.15 | 4.5 | 4.4 |

(Scores in the last two columns are means over passing patches. L6 has only 6 passing patches.)

L3 writes the best passing code, with L7 close behind. In the first grading pass, over the first L7
sample, L7 had ranked best (0.12 passing, conventions 4.8); the rerun's patches score a little
lower, in line with its lower pass rate. Search levels are close behind on passing patches
(0.21-0.22) but produce more failures. On the trap task the judge put L7 grep's reuse-and-fix first
of 12 and ranked L7 hybrid's fix third, marking down its `pick_box` change: it ranks working,
in-scope code above reuse, as the rubric asks.

The small per-configuration counts (2-20 graded runs) make per-variant judged differences noise for
now; the level pattern is the signal. `cbench report` has the per-configuration table.

## Next

1. Try a cheaper judge (agy) on a few tasks and compare it with the Claude judge.
2. Tune speculative decoding (4-5 draft tokens) on a thinking-on sample.
3. Final round: `cbench run --clean --thinking`, one run at a time, server restarted before each.
4. More tasks: 10 reuse, 5 cross-file, 3 convention, 3 control, and a second trap.
