# context-bench — notes for Claude Code

## What this project is

A small, controlled experiment harness: the same coding tasks, the same model, and nine different ways
of building the model's context (the levels below). For each run it records:

- **Correctness:** do the task's hidden tests pass?
- **Reuse:** did the model call the existing project helper the task needs, or write its own version?
- **Hallucination:** did it reference project code that doesn't exist?
- **Visibility:** was the needed helper's definition actually in what the model saw (`helper_in_context`)?
- **Cost:** input/output tokens, latency, tool calls.

The harness measures; it doesn't argue for an outcome. Implement every level as well as it can
reasonably be done, with no thumb on the scale for or against any strategy.

## How work happens in this repo

- **This is a skeleton.** Every function body is `raise NotImplementedError` with a docstring that is the
  spec. Implementations are added **one piece at a time, on request**. Implement only what you're asked
  to; don't fill in neighbouring stubs along the way.
- **Each level lives in its own module (`context_bench/levels/lN_*.py`) and is self-contained.**
  Duplication between level modules is intentional, so each strategy can be read and studied on its own.
  Don't extract shared helpers across level modules or make one level import another.
- Shared infrastructure (chunking, retrieval, apply, evaluation, runner) is fair game for normal reuse.
- Keep code small and readable; prefer the standard library.

## Hard rules

1. **Only the `<context>` block varies between levels.** System prompt, task text, output format,
   temperature and max output tokens are identical. `context_bench/prompt.py` is the single template.
2. **The base prompt never mentions reuse.** The reuse hint exists only in the `reuse_hint` ablation arm.
3. **Never truncate context silently.** If a prompt exceeds the model window, record `ctx_fits=false` and
   label `no_fit`. That's a result, not an error.
4. **No LangChain / LlamaIndex / agent frameworks.** Chunking, tokenization, ranking and prompt assembly
   are the variables under study, so the project owns them. Plain HTTP against OpenAI-compatible endpoints.
5. **Task text never names the helper.** Tasks use product language.
6. **Hidden tests live outside the repo** (`tasks/<id>/hidden_tests/`) and never enter the context.
7. Every run is one JSON line in `results/results.jsonl`; sweeps resume by `run_id`.

## Levels

| Level | Module | Context |
|---|---|---|
| L0 | `l0_no_context` | Task text only. **Control tasks only** (baseline for hallucinated references). |
| L1 | `l1_one_file` | Target file |
| L2 | `l2_manual_files` | Target + its direct imports + its test file (deterministic "what a dev would add") |
| L3 | `l3_whole_project` | Every source file, sorted, with paths. Run at repo sizes S/M/L |
| L4 | `l4_file_rag` | Target + top-3 files by retrieval (`retriever`) |
| L5 | `l5_function_rag` | Target + top-8 functions with path, import header, signature, docstring, body (`retriever`, `query_mode`) |
| L6 | `l6_repo_map` | Target + one-line signatures of every function/class |
| L7 | `l7_symbol_graph` | Target + definitions of referenced symbols + 1-hop callers |
| L8 | `l8_agentic` | Target + tools `grep`, `list_files`, `read_file` (≤15 calls); `hybrid` adds `semantic_search` |

L5 query modes: `task_vs_code`, `task_vs_summary` (cached one-line summaries per function),
`draft_then_search` (model drafts code first; the draft is the query; draft tokens count toward cost).

Ablation: `prompt_arm = reuse_hint` on L1 and L5.

Optional model axis: an older and a current model of the same family and size, same quantization, on
L1/L2/L3/L5/L8, to separate the effect of the model from the effect of context building.

## Labels and detector
`reused` · `duplicated` · `inlined_dup` · `hallucinated` · `broken` · `tool_error` · `no_fit` · `ok`.
Precedence and layers are documented in `context_bench/evaluate/detector.py`. Equivalence = new function
vs helper on fuzzed inputs (`context_bench/evaluate/fuzz.py`, one strategy per helper in
`helpers/registry.toml`), ≥ 0.95 agreement → `duplicated`.

## Retrieval
Everything goes through the `Retriever` protocol (`context_bench/retrieval/base.py`). Reference
implementations (BM25, brute-force cosine) double as yardsticks: any third-party retriever is validated
against them with `cbench validate-retrieval` (recall@k of planted helpers) before a sweep. The embedder
is swappable and recorded per run.

## Fixtures and tasks
- `fixtures/repo_S/` — seed test repo (`shopkit`) with planted helpers. Target: ~60 files, 25 helpers
  (half with names matching task wording, half not; ⅓ in `utils/`, the rest in domain modules; none in
  the target file of the task that needs it; for L2, half the reuse tasks import the helper's module
  directly and half don't). Larger M/L repos add realistic filler and leave helpers untouched.
- `tasks/<id>/task.toml` + `hidden_tests/`. Target: 40 tasks — 20 reuse, 8 cross-file, 6 convention,
  6 control. `reuse-001` is the worked example.
- `helpers/registry.toml` — every planted helper with its fuzz strategy and naming style.

## Commands (once implemented)
```
pip install -e '.[dev]' && pytest
cbench validate-retrieval --retrievers bm25,embed
cbench one --task reuse-001 --level L1
cbench run
cbench report
```
Endpoints and model names are placeholders in `config/experiment.toml`.
