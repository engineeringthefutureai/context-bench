# context-bench — notes for Claude Code

## What this project is

A small, controlled experiment harness: the same coding tasks, the same model, and nine different ways
of building the model's context (the levels below). For each run it records:

- **Correctness:** do the task's hidden tests pass?
- **Reuse:** did the new code actually call the existing project helper the task needs? (a hidden reuse test)
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
| L0 | `l0_no_context` | Task text only. **Tier-1 control tasks only** (baseline: what the model does with no repo at all). |
| L1 | `l1_one_file` | Target file |
| L2 | `l2_manual_files` | Target + its direct imports + its test file (deterministic "what a dev would add") |
| L3 | `l3_whole_project` | Every source file, sorted, with paths |
| L4 | `l4_file_rag` | Target + top-3 files by retrieval (`retriever`) |
| L5 | `l5_function_rag` | Target + top-8 functions with path, import header, signature, docstring, body (`retriever`, `query_mode`) |
| L6 | `l6_repo_map` | Target + one-line signatures of every function/class |
| L7 | `l7_symbol_graph` | Target + definitions of referenced symbols + 1-hop callers |
| L8 | `l8_agentic` | Target + tools `grep`, `list_files`, `read_file` (≤15 calls); `hybrid` adds `semantic_search` |

L5 query modes: `task_vs_code`, `task_vs_summary` (cached one-line summaries per function),
`draft_then_search` (model drafts code first; the draft is the query; draft tokens count toward cost).

Ablation: `prompt_arm = reuse_hint` on L1 and L5.

**Current scope: one model (Qwen 3.8 27B), one shot per config (seed 1), server-default temperature.** Crude on purpose;
more seeds, a second size and the model axis come later.

Optional model axis (later): an older and a current model of the same family and size, same quantization, on
L1/L2/L3/L5/L8, to separate the effect of the model from the effect of context building.

## Labels and reuse
Label: `no_fit` > `tool_error` > `broken` (a correctness test failed) > `ok`.
Reuse is a separate field, `reuse_passed` (reuse tasks only): each reuse task has
`hidden_tests/test_reuse.py`, which spies on the needed helper (`tasks/_shared/reuse_spy.py`) and checks
the new code really calls it. It is reported apart from the correctness tests, so "correct but
re-implemented" stays visible. Kept simple on purpose: no duplication detector for now; a
"plagiarism scanner" for copied/re-implemented helpers may come later.

## Grading (blind, comparison-based)
Tests give the labels and reuse; grading adds a judged view of code quality and never changes
them. Every run saves its patch and logs in `results/runs/<run_id>/` (`context_bench/bundle.py`).
`cbench grade` (`context_bench/grade.py`) shows one Claude Code judge, per task, all candidate patches
side by side, numbered, with the original repo and `grading/rubric.md`, in a directory outside this
project. The judge never learns how any candidate was produced (level, model, prompt, context, tools);
`[grading].judge_command` confines it to read-only tools inside that directory, with no settings or
CLAUDE.md loaded.

## Retrieval
Everything goes through the `Retriever` protocol (`context_bench/retrieval/base.py`). Reference
implementations (BM25, brute-force cosine) double as yardsticks: any third-party retriever is validated
against them with `cbench validate-retrieval` (recall@k of planted helpers) before a sweep. The embedder
is swappable and recorded per run.

## Fixtures and tasks
- `fixtures/repo_S/` — seed test repo (`shopkit`) with planted helpers. Target: ~60 files, 25 helpers
  (half with names matching task wording, half not; ⅓ in `utils/`, the rest in domain modules; none in
  the target file of the task that needs it; for L2, half the reuse tasks import the helper's module
  directly and half don't). It's the only repo for now; small is fine.
- `tasks/<id>/task.toml` + `hidden_tests/`. Target: 40 tasks — 20 reuse, 8 cross-file, 6 convention,
  6 control. `reuse-001` is the worked example.
- Every task also has a `tier` (difficulty ladder, orthogonal to category):
  1 spec-only new function (control) · 2 local edit, all info in the target file (control) ·
  3 reuse, one helper, name matches task wording · 4 reuse, opaque naming / several helpers ·
  5 convention defined in other files · 6 cross-file feature needing coordinated edits.
  Seed ladder: `control-001`, `control-002`, `reuse-002`, `reuse-001`, `convention-001`, `cross-001`.
- Every task is verified before commit: the unmodified fixture fails the hidden tests and a reference
  solution passes (`tasks/<id>/solutions/reference.diff`). Tasks that depend on repo context (tier 3+)
  also keep a plausible context-blind solution that fails (`context_blind.diff`). Reuse tasks: the
  reference passes `test_reuse.py`, the context-blind solution fails it.
- `helpers/registry.toml` — every planted helper and its naming style (for analysis and retrieval recall).

## Commands (once implemented)
```
pip install -e '.[dev]' && pytest
cbench validate-retrieval --retrievers bm25,embed
cbench one --task reuse-001 --level L1
cbench run
cbench report
```
Model under test: Qwen 3.8 27B, `Qwen3.8-27B-UD-Q6_K_XL.gguf` (KV cache q8_0), 128k context (`config/experiment.toml`);
the endpoint is a local placeholder.
