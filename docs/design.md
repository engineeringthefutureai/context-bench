# Design notes

## Questions
1. How do correctness and reuse of existing code change across the levels?
2. When the model doesn't reuse an existing helper, was that helper visible to it?
3. What does each level cost in tokens and latency for what it delivers?
4. (Optional model axis) How much of any difference comes from the model and how much from context
   building?

## Metrics
- Hidden-test pass rate.
- Reuse on reuse tasks: a hidden test checks the needed helper is actually called (`reuse_passed`).
- `helper_in_context`, and retrieval recall@k split by whether task wording matches the helper's name.
- Input/output tokens, latency, tool calls, and peak context usage (largest single prompt vs the window).
- A hand-checked sample of judge grades.

## Controls
One prompt template; base arm never mentions reuse; first pass: one model, one shot per config,
server-default temperature (crude on purpose; more seeds, sizes and CIs later);
private purpose-built repo (no memorization); no silent truncation; task text in product language;
whole-file output format (`<file path="...">`) instead of diffs.

## Grading
One blind Claude Code judge per task compares all candidate patches at once (`context_bench/grade.py`,
`grading/rubric.md`): numbered candidates, original repo, test counts, nothing about how each was
produced. Output: an overall ranking and 1-5 scores per dimension. A hand-graded sample checks it.

## Charts
1. Outcome by level, stacked by label
2. Outcome vs input tokens (cost frontier)
3. Context usage per level (peak prompt tokens vs the context window)
4. Outcome when the helper was visible vs not visible
5. (model axis) outcome by level, one line per model
6. Pass rate by tier × level (where each context strategy stops coping as tasks need more of the repo)
7. Judged rank and dimension scores per configuration

## Decisions
- 2026-11-21: the symbol-graph level (old L7) is deprioritized; the agentic level moved from L8
  to L7, giving eight levels L0-L7. Notes below use the new numbering throughout.
- No LangChain: retrieval details are the variables under study.
- Third-party retrievers plug in behind `Retriever` and are validated against the reference ones.
- Level modules are self-contained; duplication between them is intentional for readability.
- No duplication detector for now (simple first). Reuse = a spy-based hidden test; the judge covers how a
  non-reusing patch re-implemented things. A "plagiarism scanner" may come later.

## Later (noted, not scheduled)
- **Tasks that trip full context (L3).** On repo M (~37k tokens) the 27B at L3 passes everything,
  decoys and dead code included (2026-11-20). Once the core levels exist, design prompts where
  seeing everything is not enough (e.g. many plausible candidates, conflicting conventions). If L3
  still wins on quality, the comparison becomes efficiency: tokens_in and latency per passing run.
- **Trap reuse tasks** (with L6): a helper whose signature/docstring promises one thing while its
  body has drifted (ignored parameter, stale docstring). Correct = reuse it *and* fix it. Signature-
  only context (L6) should fall for it; body-level context (L3, L5, L7) can notice. Needs an
  M-only helper, so the task runs on repo M only.
- **Thinking on/off.** Measured on the 27B (L0-L3): thinking off passes 9/31 vs 20/31, mostly by
  breaking the output protocol (pretend tool calls, broken <file> tags), not by worse code; 22x fewer
  output tokens. Keep thinking on for results; `--no-thinking` stays available as an arm.

## Retrieval: who decides what to look for
Three points on one axis, cheapest first. The open question: how much of C's quality does B recover?

| | Who decides what to retrieve | Model calls | Can correct itself |
|---|---|---|---|
| A: L5 `task_vs_code` | the harness, from the task text | 1 | no |
| B: L5 `model_queries` | the model, once, upfront | 2 | no |
| C: L7 agent | the model, as it goes | up to 16 | yes |

- A's weakness is built in: recall depends on the task's wording, so opaque helpers ("product code"
  vs `normalize_sku`) may never be found, at least with BM25.
- B gives the model's urge to explore a channel: with thinking off, partial-context runs often
  wrote their own queries ("Let me search for how slugs are generated", pretend `search_files`).
  Same budget as A (8 chunks), same final template; planning tokens count toward cost.
- C is the upper bound: it can follow references and tell live code from dead (`find_references`,
  import counts in `list_files`).

Build order: Retriever protocol + BM25 + `cbench validate-retrieval` (recall@k of the planted
helpers per query mode; for A this needs no model at all) → L5 A (BM25) → L5 B → embedder + embed
variants → L4 (same retriever, file level) → L7.

**Baseline, A + BM25 (`cbench validate-retrieval`, 2026-11-20).** Query = task text, target file left
out. Matching helpers rank #1 as functions on S and M (`slug_for_title`, `format_weight`). Opaque
ones are invisible on M: `normalize_sku` ranks 89th and `format_money` 271st of 450 matching
chunks for reuse-001 ("product codes ... cleaned up", "invoices" share no token with either name or
body). On S (29 chunks) they still scrape in at 6 and 15. No stemming (`codes` != `code`), on
purpose for now. This is the gap B (model_queries) and the embed variants are meant to close.

**L7 thinking off vs on (27B FP8, prompt v3, S+M, 2026-11-20).** Both 11/13. trap-001: both reuse
`format_weight` *and* fix its lb branch (4/4) - the first thinking-off run to fix the drift; every
single-shot level with thinking off trusted it. Reuse tasks 5/5 with reuse in both arms. No patch
touched dead code; 3 runs read a dead file once and moved on. Cost: thinking off spends the whole
budget (15 calls every run, ~40k tokens in, ~0.9k out, ~100 s); thinking on uses ~11 calls, ~20k in,
~6.3k out, ~250 s. A real tool loop makes up for thinking off on this set; it pays in input tokens
instead of output tokens.

**Embeddings and L7 hybrid (thinking off, prompt v3, S+M, 2026-11-20).** L5 bm25 7/13, L5 embed 6/13,
L7 grep 11/13, L7 hybrid 13/13. The query is what matters, not the retriever: with the task text,
both retrievers miss the opaque helpers on M (validate-retrieval); with the model's own query
("clean product code SKU normalization") the same embedder ranks `normalize_sku` first, with the
decoys `cleanSKU` (dead) and `sku_code` (dead) right behind it, and the agent picked the live one.
Search surfaces candidates; judging them is the model's job (decoys in results are not a retrieval
failure). Hybrid used semantic_search once per reuse/trap task and was cheaper than grep on average
(32k vs 40k input tokens), finishing early when the first query hit.

**L5 model_queries (thinking off, prompt v3, S+M, 2026-11-21).** Passing: bm25 7/13 (task_vs_code
7/13), embed 8/13 (6/13). Recall moves, pass rate barely: reuse-001 on M goes from recall 0.0 to
0.5 (bm25) / 1.0 (embed) - the model's queries ("product code clean normalize sku format",
"format_money cents invoice line_total") find what the task text can't. The remaining failures are
the single-shot thinking-off ones (format, sloppy code, trusting the trap helper: 2/4 with reuse in
every L5 variant even with its body in context). The model writes queries like grep patterns
("make_slug|slugify|to_slug", "search: def format_weight") and guesses plausible names from priors.
Cost: ~3k input tokens and ~20 s per run vs L7 hybrid's 32k and 64 s.

**Full thinking-off rerun (prompt v3, 152 runs, 2026-11-21).** Second sample of every built config.
31 of 152 runs flipped against the first sample (17 ok->broken, 14 broken->ok): a single config's
count moves by about +-3 of 13 between samples, so only differences larger than that mean anything.
Stable across both samples: L7 grep/hybrid 10-13/13, L3 9/13, L5 6-10/13, L6 2-6/13, L0-L2 0-2.
trap-001 identical to before: only L7 (both variants) reuses and fixes; L2 writes its own conversion
(4/4, no reuse); everything else trusts the helper (2/4 with reuse). Performance: vLLM 14 tok/s per
request, 53 combined at concurrency 4 (long L7 prompts); actual prefix-cache hits 82% for L3 on M
(the repo prefix is shared by every task), 31-48% for L7 vs 70-74% cacheable (1024-token blocks).
Embedder (iGPU): ~600 tokens/s cold, repo M indexed once in 75 s, a new query 40-200 ms; with a
warm cache it adds nothing measurable per run. L7 peak prompt per turn: 7-8.5k tokens (vs 42k for
L3 on M); a bug that recorded L7's total input as its peak is fixed and backfilled.

**L4 file RAG (thinking off, prompt v3, S+M, 2026-11-21).** bm25 7/13, embed 6/13 (L5 task_vs_code:
8/13 in the same sweep generation), ~1.5-2.8k input tokens. Visibility misses a real path to reuse:
three L4 runs reused a helper whose definition was *not* in context (helper_in_context false) because
a retrieved file imported and called it (e.g. invoices/render.py for format_money / normalize_sku) and
the model copied the import. Seeing a helper *used* is enough for reuse; a later metric could record
"usage visible" next to "definition visible".
