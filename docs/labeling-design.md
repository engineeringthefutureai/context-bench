# Labeling results (design, 2026-11-24)

How each run gets its outcome and its badges, which parts are exact and which are judged, and how the
judged parts were checked. Grading (a judge reading the code) is separate and never changes any of this:
`docs/penalty-grading-design.md`.

Code: `context_bench/apply.py` (`format_status`), `context_bench/runner.py` (labels, lenient scoring),
`scripts/build_site.py` (`outcome()`), `context_bench/badges.py` (badges).

## Three layers, from certain to judged

| Layer | What it says | How it's decided | Certainty |
|---|---|---|---|
| Stored label | ok / broken / tool_error / no_fit | tests and the output protocol, at run time | exact |
| Outcome (reader-facing) | PASS, FAIL, UNPARSABLE, ASKED TO EXPLORE, INVALID, OUT OF TOKENS, NO FIT | a pure function of stored fields | exact |
| Badges | exact ones (BAD FORMAT, RETRIED: FORMAT, ...) and intent ones (DECLARES COMPLETION, ...) | exact: stored fields; intent: embeddings | exact / high or medium confidence |

Stored labels never change once recorded (run ids, rescore and grading depend on them). Outcomes and
badges are computed from stored fields when the site is built, so renaming or splitting an outcome
never touches a result.

## Stored label and format status

At run time the harness parses the reply strictly: complete `<file path="...">` blocks only. Then:

- `no_fit`: the prompt didn't fit the window (never truncated; rule 3).
- `tool_error`: an agent run ended on a harness-side tool failure.
- `ok` / `broken`: the patch was applied and the hidden tests passed / failed. A reply without valid
  file blocks applies nothing and is `broken`.

Every run also records `format_status` (`apply.format_status`), checked in this order:

1. `ok`: strict file blocks found.
2. `tool_request`: a pretend tool call written as text (`<function_calls>`, `<invoke`, `<tool_call>`,
   `<read_file` ...).
3. `bad_format`: code is there but not in strict blocks (a fence, an unclosed or fenced `<file>` tag,
   lines starting with `def` / `class` / `import`).
4. `no_code`: prose only.

For `bad_format`, the leniently extracted code is also run against the hidden tests (`lenient_passed`,
`lenient_total`, `lenient_reuse`), for learning only: it shows how often a format slip hid working code.
The label stays strict.

## Outcomes

`build_site.outcome()`, first match wins:

| Outcome | Rule | Meaning |
|---|---|---|
| NO FIT | label `no_fit` | the prompt is larger than the model's window |
| OUT OF TOKENS | error says `max_output_tokens` | the reply hit the output limit |
| UNPARSABLE | `format_status = bad_format` | code is there, not in the required blocks |
| ASKED TO EXPLORE | `format_status` is `no_code` or `tool_request` (not a tool error) | no code; the reply asks to look at the repository first, in prose or as a pretend tool call |
| INVALID | anything else without an accepted answer (`tool_error`) | e.g. an agent's harness-side failure |
| PASS | label `ok` | every hidden test passes |
| FAIL | otherwise | a valid answer, a hidden test fails |

Never "broken" in reader-facing text: it reads like an infrastructure problem. Infrastructure errors
(server down, timeouts) are not an outcome: those runs aren't recorded and are redone.

**Why UNPARSABLE and ASKED TO EXPLORE were split out (2026-11-24).** Until then both were INVALID. In the
v4 thinking-off round, 167 of 708 runs were INVALID, and reading them showed two different things:
a format slip (37) and a model that didn't answer but asked to look around first (130). Lumping them
hid that four out of five "invalid" answers were the second kind, which is a behaviour (a level without
tools can't explore, and the system prompt says so) rather than sloppiness. Both new outcomes come from
`format_status`, so they're exact.

**Why "asked to explore" and not "request for information".** No such reply asks the user anything.
Checked with embeddings (below) and a pattern search: 0 of 231 address the user ("could you", "please
provide", ...); all 231 announce that the model will look at the code itself.

## Badges

Badges add detail to a run without touching its outcome. `cbench badges` writes `results/badges.jsonl`.

**Exact badges** come from stored fields: NO CODE, PRETEND TOOL CALL, BAD FORMAT (from `format_status`),
OUT OF TOKENS, RETRIED: FORMAT, RETRIED: SYNTAX, SYNTAX ERROR IN PATCH, TESTS ADDED, USES DEAD CODE.

**Intent badges** describe what the final answer's prose says: DECLARES COMPLETION, CLAIMS VERIFICATION,
NOTES MISSING CONTEXT, ASKS FOR INFORMATION, WANTS TO EXPLORE, STATES A PLAN, CONCEDES, LEAVES WORK
UNFINISHED, STATES REUSE. Method:

1. The final prose: the last reply without tool calls (agents) or the response, with `<file>` blocks,
   tool-call blocks and code fences removed. Split into sentences (and list items); fragments under 12
   characters dropped, at most 40.
2. Each sentence and 3-5 short example phrases per label are embedded with EmbeddingGemma's
   classification format (`task: classification | query: `).
3. A sentence goes to its best label only if it beats the runner-up label by a margin. Absolute
   similarities don't separate (Gemma puts nearly every sentence near 0.8 to every label), relative ones
   do.
4. Margin at least 0.10 is high confidence, 0.06-0.10 medium. CONCEDES, LEAVES WORK UNFINISHED and ASKS
   FOR INFORMATION count only at high: the calibration data had no real cases and low margins gave wrong
   hits.

Calibrated on the archived v4 runs (2026-11-23): at 0.10 the sampled hits were all right, at 0.06-0.10
mostly right for the common labels. The viewer shows each badge with its sentence and confidence.

## Embedding analysis of the no-answer runs (2026-11-24)

The analysis behind the outcome split, and a reusable "dodge" centroid.

**Clustering.** All 167 INVALID runs of the round, embedded with EmbeddingGemma
(`task: clustering | query: `): the final prose, or the start of the raw reply when the prose is empty
(code-only replies). Cosine k-means, k = 7, best of 20 seeds. The clusters read as: four of "let me
explore the repository" in different words (108 runs), one of pretend tool calls (28, but also 6 replies
with a plan followed by fenced code), and two of fenced or mis-wrapped code (31).

Lesson: clusters group by prose style, not by what the reply contains. The 6 "plan, then fenced code"
replies sat with the tool calls because both open with an explanation. The outcome rule therefore comes
from the recorded protocol fields, and embeddings serve as the check.

**The dodge centroid.** Built from the 130 ASKED TO EXPLORE replies of the current round, then tested on
replies it had never seen:

| Similarity to the centroid | median | lowest |
|---|---|---|
| held-out dodges (95, rounds v3 and v4 with Qwen3) | 0.895 | 0.849 |
| prose that comes with real answers (343, PASS and FAIL runs) | 0.850 | 0.798 |
| UNPARSABLE replies (122) | 0.824 | 0.741 |

Held-out AUC 0.96. At a threshold of 0.874 it catches 92% of the held-out dodges and clears 92% of the
rest. The "real answers" closest to it say "I need to understand the codebase before making changes. Let
me explore the files..." and then deliver code in the same reply: explore language, real answer. So its
precision on explore *language* is better than 92%, and it can measure something the outcome can't: a
model announcing that it wants to explore and answering anyway.

Caveats: the centroid is specific to EmbeddingGemma 2 and its prefix (another embedder needs a new one,
built the same way); it was built on one model's (Qwen 3.8 27B, thinking off) phrasing, so a different
model's dodges should be checked against it before trusting the threshold.

## Rules of thumb

- Prefer an exact rule from recorded fields; use embeddings to find structure and to check the rule.
- A judged label always carries its confidence, and is never folded into an outcome.
- Validate on held-out data: a different round, never only the data a threshold was tuned on.
- Never change a stored label; add a derived field or a display mapping instead.
- Every new judged label gets the same treatment: examples or a centroid, a margin or threshold, a
  sample read by hand, and a note here.
