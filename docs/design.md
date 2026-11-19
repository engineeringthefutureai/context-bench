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
- No LangChain: retrieval details are the variables under study.
- Third-party retrievers plug in behind `Retriever` and are validated against the reference ones.
- Level modules are self-contained; duplication between them is intentional for readability.
- No duplication detector for now (simple first). Reuse = a spy-based hidden test; the judge covers how a
  non-reusing patch re-implemented things. A "plagiarism scanner" may come later.
