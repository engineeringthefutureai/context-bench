# Design notes

## Questions
1. How do correctness, reuse of existing code and hallucinated references change across the levels?
2. When the model writes its own version of an existing helper, was that helper visible to it?
3. What does each level cost in tokens and latency for what it delivers?
4. Does whole-project context hold up as the repo grows (sizes S / M / L)?
5. (Optional model axis) How much of any difference comes from the model and how much from context
   building?

## Metrics
- Hidden-test pass rate.
- Reuse outcome on reuse tasks: reused / duplicated / inlined duplicate.
- Hallucinated references to project code that doesn't exist.
- `helper_in_context`, and retrieval recall@k split by whether task wording matches the helper's name.
- Input/output tokens, latency, tool calls.
- 10% stratified hand audit of labels to report detector precision.

## Controls
One prompt template; base arm never mentions reuse; 3 seeds at temperature 0.2 with bootstrap CIs;
private purpose-built repo (no memorization); no silent truncation; task text in product language;
at least two model sizes; whole-file output format (`<file path="...">`) instead of diffs.

## Charts
1. Outcome by level, stacked by label
2. Outcome vs input tokens (cost frontier)
3. L3 outcome vs repo size
4. Outcome when the helper was visible vs not visible
5. (model axis) outcome by level, one line per model

## Decisions
- No LangChain: retrieval details are the variables under study.
- Third-party retrievers plug in behind `Retriever` and are validated against the reference ones.
- Level modules are self-contained; duplication between them is intentional for readability.
