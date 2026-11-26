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
| Outcome (reader-facing) | PASS, FAIL, UNPARSABLE, ASKED TO EXPLORE, NO ANSWER, STOPPED EARLY, INVALID, OUT OF TOKENS, OUT OF TIME, NO FIT | a pure function of stored fields | exact |
| Badges | exact ones (BAD FORMAT, SECOND CHANCE, ...) and intent ones (DECLARES COMPLETION, ...) | exact: stored fields; intent: embeddings | exact / high or medium confidence |

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
2. `empty`: nothing at all.
3. `tool_request`: a pretend tool call written as text (`<function_calls>`, `<invoke`, `<tool_call>`,
   `<read_file` ...) **without code** in it.
4. `bad_format`: code is there but not in strict blocks (a fence, an unclosed or fenced `<file>` tag,
   lines starting with `def` / `class` / `import`), including code written inside a pretend tool call.
5. `no_code`: prose only.

For an agent run the status is judged on its last non-empty reply: when the final reply is empty (an
agent that answered the format retry with nothing), the reply before it decides.

**Why (2026-11-24, from reuse-L03 · L7 rich · L).** The agent wrote "I have everything I need..." and the
complete code, wrapped in a pretend `<tool_call>` instead of a `<file>` block; the harness's format retry
got an empty reply; the run was judged on that empty reply (`no_code`) and showed as ASKED TO EXPLORE,
which it plainly wasn't. Two rule fixes: a pretend tool call that carries code is a malformed answer
(UNPARSABLE), not a request to explore; an empty final reply never hides the answer before it, and an
empty reply on its own is `empty` (NO ANSWER, badge EMPTY REPLY). The dodge prototypes had it right all
along: they score that reply as answer-like (-0.017), no WANTS TO EXPLORE badge. Same pattern elsewhere:
2 single-shot replies with code in a pretend tool call and 1 empty agent reply in the v4 round, 7 empty
agent replies in the earlier rounds. Found at the same time: `format_retry` was only recorded for the
selective variant; now for every agent run, and `cbench rescore` backfills it from the transcripts.

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
| ASKED TO EXPLORE | `format_status` is `no_code` or `tool_request`, at a level without tools (L0-L6) | no code; the reply asks to look at the repository first, in prose or as a pretend tool call |
| STOPPED EARLY | an agent (L7, L8, L-AG) with `format_status` `no_code` or `tool_request`, no time limit hit | the agent ended without code or a change, typically announcing its next step in prose without the call (2026-11-26: L-AG long-005@L, reuse-001@L) |
| OUT OF TIME | an agent with no answer whose `error` says the harness's limit ended it ("timed out after", "stopped by the harness") | L-AG's 1 h or 100-call cap; a run that already had a passing change keeps PASS |
| NO ANSWER | `format_status = empty` | the reply was empty: an agent that went silent |
| INVALID | anything else without an accepted answer | a run whose reply doesn't say (a harness failure) |

**The reply decides, not the stored label (2026-11-24).** An agent's stored label is `tool_error` when any
tool call failed along the way (a bad argument, a call over budget). Those calls get feedback and the run
goes on, so the label says nothing about how the run ended. Two L7 selective runs (qwen names) that went
silent showed as INVALID only because one call had failed earlier; they are NO ANSWER.
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

**Why NO ANSWER (2026-11-24, from control-001 · L7 selective · M).** That agent read
`shopkit/utils/collections.py` six times (five in a row), spent its budget, then answered both "reply with
your <file> blocks" prompts with nothing. Seven agent runs across the three rounds never wrote a word and
ended silent; most first repeated one identical tool call 6 to 12 times. Not a refusal (nothing declines
anything; a reply that says it can't do the task gets the CONCEDES badge) and not a request to explore: a
session that degenerated. The user proposed REFUSED; NO ANSWER was chosen as more accurate. The exact
badge LOOPED marks any agent run that repeats an identical tool call 3 or more times, whatever its outcome.

## Badges

Badges add detail to a run without touching its outcome. `cbench badges` writes `results/badges.jsonl`.

**Exact badges** come from stored fields: NO CODE, PRETEND TOOL CALL, BAD FORMAT, EMPTY REPLY (from `format_status`),
LOOPED (an identical tool call 3 or more times),
OUT OF TOKENS, SECOND CHANCE (L7/L8: the agent tried to finish with an answer the harness rejected and was asked
once more: no usable <file> blocks or no edits, changed files that don't parse, or a tool call after the tools
were gone; replaces RETRIED: FORMAT and RETRIED: SYNTAX, 2026-11-25), SYNTAX ERROR IN PATCH, TESTS ADDED, USES DEAD CODE.

**Intent badges** describe what the final answer's prose says: DECLARES COMPLETION, CLAIMS VERIFICATION,
NOTES MISSING CONTEXT, ASKS FOR INFORMATION, STATES A PLAN, CONCEDES, LEAVES WORK UNFINISHED, STATES REUSE
(example phrases, below), and WANTS TO EXPLORE (dodge prototypes, next section). Method for the
example-phrase labels:

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

The analysis behind the outcome split, and the measured WANTS TO EXPLORE badge: first one centroid, then
prototypes on both sides.

**Clustering.** All 167 INVALID runs of the round, embedded with EmbeddingGemma
(`task: clustering | query: `): the final prose, or the start of the raw reply when the prose is empty
(code-only replies). Cosine k-means, k = 7, best of 20 seeds. The clusters read as: four of "let me
explore the repository" in different words (108 runs), one of pretend tool calls (28, but also 6 replies
with a plan followed by fenced code), and two of fenced or mis-wrapped code (31).

Lesson: clusters group by prose style, not by what the reply contains. The 6 "plan, then fenced code"
replies sat with the tool calls because both open with an explanation. The outcome rule therefore comes
from the recorded protocol fields, and embeddings serve as the check.

**The dodge centroid (first version).** Built from the 130 ASKED TO EXPLORE replies of the current round,
then tested on replies it had never seen:

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

**Why one centroid wasn't enough.** Used as a badge, the single centroid had two weaknesses, both found by
reading every hit:

- **Dodges come in variants**, and their average is a blur: a generic "let me explore the repository", "I'll
  look at the relevant files", "I need to understand how X works, let me look", and pretend tool calls. A
  reply close to one variant can sit far from the average.
- **Nothing to compare against.** A single centroid answers "how dodge-like is this?" with no idea what
  else a reply could be. To EmbeddingGemma, "Now I have enough context. Creating the module:" is a short
  sentence about the next step, just like "Let me look at the helpers first", and scored as high.

Adding a single centroid for answer prose and scoring the difference didn't fix it (held-out AUC 0.93, worse
than one centroid's 0.96 on that test set): real-answer prose has variants too (plans, "the task is clear",
"creating the module now"), and its average is a blur as well.

**The fix: prototypes on both sides** (the user's idea: several centroids, bucketed by distance). Cosine
k-means splits the dodges into K_DODGE prototypes and the prose of real answers into K_ANSWER prototypes.
A reply's score is its similarity to the nearest dodge prototype minus its similarity to the nearest answer
prototype: positive means it reads more like some kind of dodge than like any kind of answer. Tried on
held-out runs (prototypes from the current round, tested on 95 dodges and 317 others from the earlier
rounds, agents' final replies included), cutoff at 90% of training dodges:

| Dodge prototypes | Answer prototypes | Held-out AUC | Others over the cutoff |
|---|---|---|---|
| 1 | none | 0.928 | 14.5% |
| 1 | 5 | 0.939 | 16.7% |
| 3 or 5 | none | 0.90-0.92 | 19-26% |
| 5 | 5 | 0.966 | 5.0% |
| **8** | **3** | 0.958 | **3.8%** |
| 8 | 5 or 8 | 0.96 | 4.1% |

More dodge prototypes alone make it worse: they reach out to more kinds of text. The gain comes from
modelling both sides. 8 + 3 had the fewest false alarms.

**Two refinements, each found by reading the misses and hits:**

- **Answer prototypes leave out look-first talk.** Some real answers open with "let me look at ..." and then
  give the code. Learned as answer prose, that pulled real dodges ("Let me look at the relevant code to
  understand the weight formatting") toward the answer side, and 12 were missed. Answer prose with a
  look-first phrase is now excluded from the answer side: held-out AUC 0.958 -> 0.976.
- **Cutoffs come from a false-alarm budget**, not a target catch rate: medium where 5% of the non-dodges
  reach (strictly above the cutoff), high where 2% do. Calibrating "catch 90%" missed 10% of dodges by
  construction even once false alarms were down to 1%.

**In use: the WANTS TO EXPLORE badge.** The prototypes replaced the badge's 5 hand-written example phrases.
They live in `grading/dodge_prototypes.npz` (8 dodge and 3 answer vectors), with the embedder, prefix,
counts, cutoffs, validation and a representative reply per dodge variant in `grading/dodge_prototypes.json`.
`cbench badges --rebuild-prototypes` rebuilds them from every thinking-off run in `results/` and
`results/archive-*/`, using the current folder alone for the held-out check; k-means uses fixed seeds, so a
rebuild is repeatable. Prototypes built with another embedder are refused, not silently used. Each badge
records its score and the variant it matched.

Two more rules, from reading every hit:

- **An answer made only of code has nothing to compare.** The raw-reply fallback (for replies without
  prose) applies only to replies without valid code; on valid answers it had matched file headers.
- **On a reply that did answer, the badge also needs an explicit look-first phrase** ("let me look /
  check / search / explore / read ...", "I need to see / know / understand ..."). Look-first sentences are
  rare inside answers and a few borderline ones ("creating the file now", a typo fix) can still score
  positive; the phrase makes those hits exact.

The variants (first build, 226 dodges): generic exploring ("Let me start by exploring the repository to
understand the codebase structure", 54 replies; "I need to understand the code I'm changing. Let me look at
the relevant files", 27; "...I'll first explore the repository", 25), looking for one specific thing ("I
need to understand how the shop rounds percentage amounts. Let me look...", 26; the week-label format, 18;
"how domain events are announced", 20), and pretend tool calls ("I'll investigate the relevant files" +
`<function_calls>`, 49; 7 more).

**Result** (first build, 226 dodges, 300 answer prose, 122 unparsable): held-out AUC 0.976; medium
catches 96% of dodges at 5.2% false alarms, high 93% at 2.1%. On the v4 thinking-off round (732 runs):
126 of 130 ASKED TO EXPLORE runs carry the badge (121 high); the 4 misses are 3 terse dodges and one reply
that never says it wants to look ("The task is clear: ..." and no code). On runs that answered, 9 carry
it, all real look-first sentences on reading: 6 FAIL, 1 ran out of tokens, 2 PASS. Wanting to look first
goes with not having what's needed.

| | single centroid | prototypes |
|---|---|---|
| held-out AUC (same test set) | 0.928 | 0.976 |
| dodges badged (of 130) | 118, 82 high | 126, 121 high |
| hits on answered runs that were wrong | about half, before the phrase rule | none of 9 |

Caveats: the prototypes are specific to EmbeddingGemma 2 and its prefix (another embedder needs new ones,
built the same way); they were built on one model's phrasing (Qwen 3.8 27B, thinking off), so a different
model's dodges should be checked against them before trusting the cutoffs. K_DODGE and K_ANSWER were
picked on this data; a much larger or more varied set may want more answer prototypes.

## Rules of thumb

- Prefer an exact rule from recorded fields; use embeddings to find structure and to check the rule.
- A judged label always carries its confidence, and is never folded into an outcome.
- Validate on held-out data: a different round, never only the data a threshold was tuned on.
- Never change a stored label; add a derived field or a display mapping instead.
- Every new judged label gets the same treatment: examples or a centroid, a margin or threshold, a
  sample read by hand, and a note here.
