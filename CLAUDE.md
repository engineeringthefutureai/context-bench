# context-bench — notes for Claude Code

## What this project is

A small, controlled experiment harness: the same coding tasks, the same model, and nine different ways
of building the model's context (the levels below). For each run it records:

- **Correctness:** do the task's hidden tests pass?
- **Reuse:** did the new code actually call the existing project helper the task needs? (a hidden reuse test)
- **Visibility:** did the model see the needed helper? `helper_in_context` (its def line in any form:
  source, L6 signature, tool output) and `helper_body_in_context` (its full source, so it could see what it does).
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
| L6 | `l6_repo_map` | Target + one-line signatures of every function/class (`map_docs = true`: + first docstring line, "L6+docs") |
| L7 | `l7_symbol_graph` | Target + definitions of referenced symbols + 1-hop callers |
| L8 | `l8_agentic` | Target + tools `grep`, `list_files`, `read_file`, `find_references` (≤15 calls); `hybrid` adds `semantic_search` |

L5 query modes: `task_vs_code`, `task_vs_summary` (cached one-line summaries per function),
`draft_then_search` (model drafts code first; the draft is the query; draft tokens count toward cost),
`model_queries` (a planning call where the model writes up to 5 search queries from the task and
target file; top 3 per query, merged to 8; planning tokens count toward cost; the planning
instruction never mentions reuse).

Ablation: `prompt_arm = reuse_hint` on L1 and L5.

Active vs legacy code: L8 must be able to tell live code from dead code (repo M: `common/`, `utils/strings.py`).
It gets the means, not the instruction: `find_references` (where a name is used) and import counts
in `list_files`. Nothing in the prompt says dead code exists. Measured at every level:
`legacy_ctx_share` (share of context tokens from dead paths; L8: tool output) and `legacy_used`
(the patch imports or calls dead code). Dead paths are `[legacy].paths` in `helpers/registry.toml`,
harness-only. A `legacy_hint` prompt arm, if ever wanted, is an opt-in ablation like `reuse_hint`.

**Current scope: one model (Qwen 3.8 27B), one shot per config (seed 1), server-default temperature.**
During development thinking is off (`[generation].thinking = false`, labelled "nothink"); the final
round reruns everything with thinking on, in clean mode (`cbench run --clean --thinking`): one run at a
time, the model server restarted before each, so no prefix cache carries over between tasks that all
share the same repo and system prompt. Both arms keep separate run ids. Crude on purpose;
more seeds, a second size and the model axis come later.

Optional model axis (later): an older and a current model of the same family and size, same quantization, on
L1/L2/L3/L5/L8, to separate the effect of the model from the effect of context building.

## Labels and reuse
Label: `no_fit` > `tool_error` > `broken` (a correctness test failed) > `ok`.
Reuse is a separate field, `reuse_passed` (reuse tasks only): each reuse task has
`hidden_tests/test_reuse.py`, which spies on the needed helper (`tasks/_shared/reuse_spy.py`) and checks
the new code really calls it. It is reported apart from the correctness tests, so "correct but
re-implemented" stays visible.

Output protocol: the label is strict (no valid `<file>` block = failure), but every run records
`format_status` (`ok` / `bad_format` / `no_code` / `tool_request`). For `bad_format`, the leniently
extracted code is also run against the hidden tests (`lenient_passed/total`, `lenient_reuse`), for
learning only. `tool_request` = the model asked to explore (pretend tool calls). `cbench rescore`
recomputes these from saved responses without calling the model. Kept simple on purpose: no duplication detector for now; a
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
is swappable and recorded per run. Implemented: BM25 (`retrieval/bm25.py`, code-aware tokens from
`tokenize.py`) and validation with query = task text (`retrieval/validate.py`: function and file level,
target file left out, recall@1/3/8 split by naming, the first decoy that outranks a helper). Functions are
indexed as `chunk_text` (path, qualname, source), the same text L5 searches. Embeddings: `embedders.py`
(any `/v1/embeddings` endpoint, cached on disk per content under `.cache/embeddings/`) and
`retrieval/dense.py` (brute-force cosine, `query_prefix` on queries only); `[embedder]` points at
Ollama on the iGPU (`qwen3-embedding:0.6b`). L4 implements both retrievers (whole files indexed as `file_text`, like validate-retrieval). L5 implements `task_vs_code` and `model_queries` (BM25 or embed; queries merged round-robin by rank), L8 `grep`
and `hybrid`; other L5 modes raise NotImplementedError, and the sweep skips that
variant only (level + retriever + query mode + agent variant), not its siblings.

## Fixtures and tasks
- `fixtures/repo_S/` — seed test repo (`shopkit`, ~2k tokens) with the first three planted helpers.
- `fixtures/repo_M/` — the grown `shopkit` (~40.6k tokens at L3 incl. dead code and its tests, ~110 files, stdlib only, its own passing
  `tests/`). A strict superset of S: every S file is byte-identical, so every task runs on both
  (`--repo-size M`). 25 helpers (13 matching / 12 opaque naming, 9 in `utils/`), each called somewhere
  in M, plus near-miss decoys (`[[decoy]]` in the registry) that fail the hidden tests if used instead.
  Dead code is named like the canonical place, as in real repos (legacy code is rarely labelled legacy):
  `shopkit/common/` (~4k tokens, old style: camelCase, float dollars, globals) and
  `shopkit/utils/strings.py` (`slugify`, `sku_code`, `amount_str`). Only their own tests import them
  (old unittest style). Every decoy has tests. Dead paths: `[legacy]` in the registry (harness-only).
  New tasks: no helper in the target file of the task that needs it; for L2, half the reuse tasks
  import the helper's module directly and half don't. Changing an S file changes both repos.
  M deliberately has no `tests/test_<stem>.py` for existing task targets, so L2 shows them nothing extra.
- `tasks/<id>/task.toml` + `hidden_tests/`. Target: 40 tasks — 20 reuse, 8 cross-file, 6 convention,
  6 control. `reuse-001` is the worked example.
- Every task also has a `tier` (difficulty ladder, orthogonal to category):
  1 spec-only new function (control) · 2 local edit, all info in the target file (control) ·
  3 reuse, one helper, name matches task wording · 4 reuse, opaque naming / several helpers ·
  5 convention defined in other files · 6 cross-file feature needing coordinated edits.
  Seed ladder: `control-001`, `control-002`, `reuse-002`, `reuse-001`, `convention-001`, `cross-001`.
- Trap tasks (`category = "trap"`): the helper's signature/docstring promise something its body no
  longer does (documentation drift). Correct = reuse it *and* fix it. The outcomes separate without
  extra machinery: trusting the docs -> broken with reuse; own implementation -> ok without reuse;
  reuse + fix -> ok with reuse. Each keeps a `solutions/trusting.diff` that must fail. `trap-001`
  (M only): `format_weight(grams, unit)` documents "lb" but returns kg.
- Optional `repo_sizes = ["M"]` in task.toml limits a task to some repos. Config `repo_sizes =
  "smallest"` (L0-L2) runs each task once, on the smallest repo it fits.
- Every task is verified before commit: the unmodified fixture fails the hidden tests and a reference
  solution passes (`tasks/<id>/solutions/reference.diff`). Tasks that depend on repo context (tier 3+)
  also keep a plausible context-blind solution that fails (`context_blind.diff`). Reuse tasks: the
  reference passes `test_reuse.py`, the context-blind solution fails it.
- `helpers/registry.toml` — every planted helper and its naming style, plus decoys (for analysis and retrieval recall).

## Commands (once implemented)
```
pip install -e '.[dev]' && pytest
cbench validate-retrieval --retrievers bm25,embed
cbench one --task reuse-001 --level L1
cbench run                       # resumable; --level L1 --level L3 to limit, --dry-run to list what is left
cbench run --clean --thinking    # final round: serial, model server restarted before every run (cold cache;
                                 # CBENCH_RESTART_CMD in .env), --rerun to redo recorded configs
cbench rescore                   # protocol fields from saved responses, no model calls
cbench report
```
Model under test: Qwen 3.8 27B, `Qwen3.8-27B-UD-Q6_K_XL.gguf` (KV cache q8_0), 128k context (`config/experiment.toml`);
the endpoint is a local placeholder.
