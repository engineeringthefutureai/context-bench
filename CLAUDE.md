# context-bench — notes for Claude Code

## What this project is

A small, controlled experiment harness: the same coding tasks, the same model, and eight different ways
of building the model's context (the levels below). For each run it records:

- **Correctness:** do the task's hidden tests pass?
- **Reuse:** did the new code actually call the existing project helper the task needs? (a hidden reuse test)
- **Visibility:** did the model see the needed helper? `helper_in_context` (its def line in any form:
  source, L6 signature, tool output) and `helper_body_in_context` (its full source, so it could see what it does).
  `helper_usage_in_context`: the helper seen imported or called (models reuse what they only saw used).
- **Cost:** input/output tokens, latency, tool calls.

The harness measures; it doesn't argue for an outcome. Implement every level as well as it can
reasonably be done, with no thumb on the scale for or against any strategy.

## How work happens in this repo

- **Started as a skeleton; most of it is implemented now.** What remains is stubs that `raise
  NotImplementedError` with a docstring that is the spec (L5 `task_vs_summary` / `draft_then_search`,
  `build_summaries`, the deprioritized symbol graph). Implementations are added **one piece at a time,
  on request**. Implement only what you're asked to; don't fill in neighbouring stubs along the way.
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
| L7 | `l7_agentic` | Target + tools `grep`, `list_files`, `read_file`, `find_references` (≤15 calls); `hybrid` adds `semantic_search`; `rich` and `selective` (below). Prompt v4: no target, the repo's file tree instead, no `list_files` |

L8 (experimental, 2026-11-23, repo M only for now): `l7_agentic`'s `edit` variant, registered as its own
level so it never mixes into L7. Starts from the symbol list (per file: path, lines, importers, names of
functions/classes/methods/module constants, no signatures); `read_symbol` is the only reader (names, and
"path:start-end"; a bare path or module name returns the file's outline); `grep`, `find_references`,
`semantic_search`, `drop`; changes are made with `edit(path, old, new)` and `create_file` tools on the
run's repo copy (free on the budget, failed calls cost 0.5), and a reply without tool calls ends the run;
the patch is the copy's diff (`apply.changed_files`, files saved in the bundle for rescore). Budget in
"points" with a per-turn "[n of 15 points left]" line. Prompt arm `edit_tools` replaces only the output
format section: a documented exception to hard rule 1, L8 only. Unknown tools that other harnesses have
(read_file, write_file, ...) get a specific "there is no X tool" error. All L7 variants and L8 under v4
see `File: path / (This file does not exist yet.)` when the change creates a new file (as L1-L6 do).
A final reply that is a tool call after the tools were gone gets one retry (`FINAL_RETRY`).
L8 checks syntax, never runs code: after every `edit` / `create_file` of a .py file the harness compiles it
(`compile`, nothing executed) and appends a warning with the line if it no longer parses; ending the run
while a changed file doesn't parse gets one `SYNTAX_RETRY` (tools stay available). Running code during a
run (a `run_tests` or code-snippet tool) was deliberately left out of this experiment: it would let the
model execute arbitrary code on the host, and hidden tests must never be an agent's oracle (rule 6). The
harness does execute the model's code when it scores a run (hidden tests, repo tests, patch coverage):
that risk is accepted, the same as for every level.

L5 query modes: `task_vs_code`, `task_vs_summary` (cached one-line summaries per function),
`draft_then_search` (model drafts code first; the draft is the query; draft tokens count toward cost),
`model_queries` (a planning call where the model writes up to 5 search queries from the task and
target file; top 3 per query, merged to 8; planning tokens count toward cost; the planning
instruction never mentions reuse).

Ablation: `prompt_arm = reuse_hint` on L1 and L5.

Deprioritized (not a level, not registered, no config): **symbol graph** (`levels/lx_symbol_graph.py`,
spec only): target + definitions of referenced symbols + 1-hop callers, LSP-style. It was L7 until
2026-11-21, when the agentic level moved from L8 to L7 (eight levels, L0-L7). On reuse tasks the
needed helper is never referenced from the target, so it adds little over L2; revisit once there are
several cross-file / convention tasks. Results recorded as L8 were migrated to L7 (new run ids).

Prompt v4 (2026-11-23, implemented, `PROMPT_VERSION` still 3 until the full rerun starts): the shared
prompt no longer mentions a target file (no "Target file:" line, no "and the target file" in the system
prompt). L1-L6 still show the target (their context starts with its `File:` block). L7 gets no target: it
finds the file from the task, and its starting context is the repository's whole file tree (each file's
line count, importers for .py), so `list_files` (now recursive) is not offered: 97% of L7 runs opened
with list_files, about 3 calls per run. v4 means new run ids for every config (full rerun). Also from v4: L7's tool note explains the budget mechanics (failed calls
cost half, several calls per turn) and, for selective, why dropping helps; every tool's schema describes
its parameters and output (equally detailed for all variants; facts, never which tool to prefer); a
selective drop that removes at least 300 tokens gives back 0.25 of a call, taken back if the dropped code
is read again, at most 2.0 per run (`drop_refunds`, `drop_refund_clawbacks`).

L7 failed tool calls (bad arguments, unknown tool, a tool call written as text the server couldn't
parse) get generic feedback (the tool's valid arguments / "couldn't parse, use the tool-calling
interface") and the loop goes on; each costs `failed_call_cost` (0.5) of the 15-call budget. Rows from
the original harness (no feedback, a text tool call ended the run) have no `failed_call_cost` and are
labelled ` h1` in the report.

L7 `rich` (added 2026-11-22): hybrid's tools, richer, plus `read_symbol`: `semantic_search` returns ≤5 hits
with source (40 lines max); `grep` and `find_references` name each hit's enclosing function/method/class
and its lines; `read_symbol` pulls up to 8 definitions by name in one call (verbatim source, methods with
their class header); `read_file` takes a line range (≤200 lines, verbatim) and its description says
read_symbol is cheaper for partial reads (tool description, not prompt). Append-only. `selective` = rich + `drop`: every result gets an
ID (`[#n]`); only the latest batch (the "live tail") can be dropped, which puts a one-line stub in its
place for later turns, so the cached prefix survives up to the tail. drop is free on the budget; a bad
drop is a failed call. Its tool note adds one generic working-set line ("keep what helps, drop what
doesn't"; nothing about dead code or decoys). Transcripts keep the original results plus drop records
(`turn_prompts` replays the stubs); `tokens_cacheable` ends at the first rewritten message. Results are split into
segments, each with its own ID: code (one definition, one read range), notes, and a file's imports.
Imports are a separate segment the harness manages: shown once per file, the model can't drop them,
they're stubbed automatically when the file's last code in context is dropped, and shown again with
the file's next code (with a live tail this never rewrites an older message). Recorded:
`drops`, `drop_calls`, `rereads_after_drop`, `helper_body_at_answer`, `legacy_share_at_answer`,
`dropped_tokens` (size dropped, once) and `drop_saved_tokens` (input tokens avoided over all later
requests; tokens_in + this = the run without drops). The report must show the context saved by
dropping against the cache given up.
grep/hybrid behaviour is unchanged (pinned by a test). hybrid -> rich measures the tools, rich ->
selective the dropping.

Active vs legacy code: L7 must be able to tell live code from dead code (repo M: `common/`, `utils/strings.py`).
It gets the means, not the instruction: `find_references` (where a name is used) and import counts
in `list_files`. Nothing in the prompt says dead code exists. Measured at every level (`metrics.py`, backfilled by `cbench rescore`):
`legacy_ctx_share` (share of context tokens from dead paths; L7: tool output) and `legacy_used`
(the patch imports or calls dead code). Dead paths are `[legacy].paths` in `helpers/registry.toml`,
harness-only. A `legacy_hint` prompt arm, if ever wanted, is an opt-in ablation like `reuse_hint`.

**Current scope: one model (Qwen 3.8 27B), one shot per config (seed 1), server-default temperature.**
During development thinking is off (`[generation].thinking = false`, labelled "nothink"); the final
round reruns everything with thinking on, in clean mode (`cbench run --clean --thinking`): one run at a
time, with the prefix cache made cold before each, so nothing cached carries over between tasks that
all share the same repo and system prompt. Between runs the cache is dropped (`/reset_prefix_cache`,
vLLM with `VLLM_SERVER_DEV_MODE=1`, 75 ms); the server is fully restarted on the first run, every
`CBENCH_RESTART_EVERY` runs (25; L7 and L8: `CBENCH_RESTART_EVERY_L7`, 12, also in concurrent sweeps) and when host memory drops below `CBENCH_RESTART_MIN_FREE_MB` (6000),
since the reset frees no memory and the vLLM workers grow in host RAM. Each run records `cold_method`. Concurrent (not clean) sweeps also restart the server when host memory drops below
`CBENCH_RESTART_MIN_FREE_MB`: no new run starts, the runs in flight finish, one restart, then the sweep goes on. Both arms keep separate run ids. Crude on purpose;
more seeds, a second size and the model axis come later.

Optional model axis (later): an older and a current model of the same family and size, same quantization, on
L1/L2/L3/L5/L7, to separate the effect of the model from the effect of context building.

## Labels and reuse
Label: `no_fit` > `tool_error` > `broken` (a correctness test failed) > `ok`.
In the report and viewer the labels show as outcomes (stored labels unchanged): PASS (ok), FAIL
(valid answer, a hidden test failed), INVALID (no accepted answer: bad format, pretend tool calls,
tool error), OUT OF TOKENS (hit max output), NO FIT. Never "broken" in reader-facing text.
Regression check, also separate: `repo_tests_passed/total` = the repo's own `tests/` after the patch
(catches patches that pass the hidden tests while breaking or deleting code elsewhere; `cbench rescore`
backfills it from saved replies). Every task's reference solution keeps them green.
Patch coverage, also separate (`evaluate/patch_coverage.py`, stdlib line tracing, backfilled by rescore):
`patch_stmts` / `patch_stmts_covered` / `patch_coverage` = the patch's changed statements outside test
files that the hidden tests ran (a PASS with low coverage = much of the new code unverified; every
reference solution is at 100%). `tests_added` / `test_files_changed`: tests the patch adds (descriptive;
the prompt never asks for tests). Rubric v2 (2026-11-22, before the final round's grading) asks the judge
to read new logic the tests may not run; never compare grades across rubric versions.
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
`cbench grade` (`context_bench/grade.py`) shows one judge, per task, all candidate patches
side by side, numbered, with the original repo and `grading/rubric.md`, in a directory outside this
project. The judge never learns how any candidate was produced (level, model, prompt, context, tools);
`[grading].judge_command` confines it to read-only tools inside that directory. Judge: agy (plan mode,
sandbox, a session-only read-only command allow-list) since 2026-11-22, the Claude Code judge before
(cheaper; calibrated on 3 tasks). Each grade records its `judge`; never compare ranks across judges.
Penalty grading (from the v4 round, `docs/penalty-grading-design.md`): `cbench grade --mode penalty
--judge agy|claude`, one call per distinct passing patch, catalogue `grading/penalties.md`; the harness
keeps a penalty only if its code is known and its quote is a changed line; `results/penalties.jsonl`.

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
Ollama on the iGPU: `embeddinggemma-2` from 2026-11-23 (task prefixes on queries and documents:
`query_prefix`, `document_prefix`; on repo M about as good as `qwen3-embedding:0.6b`, used before: func@8 7/12
vs 8/12, file@8 9/12 vs 8/12). L4 implements both retrievers (whole files indexed as `file_text`, like validate-retrieval). L5 implements `task_vs_code` and `model_queries` (BM25 or embed; queries merged round-robin by rank), L7 `grep`
and `hybrid`; other L5 modes raise NotImplementedError, and the sweep skips that
variant only (level + retriever + query mode + agent variant), not its siblings.

## Fixtures and tasks
**Fixtures are frozen since 2026-11-22 (final round).** Don't change any file under `fixtures/`: a
changed repo_hash makes every recorded run on that repo stale and the sweep reruns them all (~20-30 h
with thinking on). New tasks must use repo M as it is (25 planted helpers; ~11 used so far).
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
- `fixtures/repo_L/` — shopkit about a year after M (2026-11-22, `docs/repo-L-design.md`): 169k tokens at L3
  (NO FIT, model tokenizer; L6 map 26k, L6+docs 32k), ~280 files incl. SQL migrations (`migrations/`) and HTML templates
  (`templates/`), its own passing tests (243). Not a byte-identical superset: M's files mostly unchanged,
  some grown. Old code keeps old names and is never labelled (`shopkit/core/`: partly live order engine,
  reporting, ERP, pricing); dead: `shopkit/domain/`, `shopkit/utils/helpers.py`, `common/`, `utils/strings.py`.
  Five incident-bloated live functions (200-270 lines, a business rule in the middle): payouts.batching,
  payments.settlement, warehouse.waves, api.webhook_delivery, pricing.cart. The workspace reads .py, .sql
  and .html (chunks stay Python-only; S and M have only .py). L must not contain any M task's feature
  (list in the design doc); every M task holds on L (`repo_sizes = ["M", "L"]`). Not in the config yet.
- `tasks/<id>/task.toml` + `hidden_tests/`. Target: 40 tasks — 20 reuse, 8 cross-file, 6 convention,
  6 control. `reuse-001` is the worked example. Now 30: 10 L-only (long-001..005: a rule buried in a long function, category `long`; reuse-L01..03; cross-L01 full stack; control-L01 long target), and reuse-001..010 (003-010 are M-only, built on
  M's planted helpers, each with a behaviour quirk a naive rewrite gets wrong), cross-001..003,
  convention-001..003, control-001..003, trap-001. Batch 2 (M-only except control-003): domain
  events via the bus, settings via Settings/load_settings, product weight and gift message
  across model + fulfillment + API serializer. `tests/test_tasks_verified.py` checks every task's
  contract (unmodified fails, reference passes + reuses, context-blind fails reuse, trap trusting fails).
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

## Commands
```
pip install -e '.[dev]' && pytest
cbench validate-retrieval --retrievers bm25,embed
cbench one --task reuse-001 --level L1
cbench run                       # resumable; --level L1 --level L3 to limit, --dry-run to list what is left
cbench run --clean --thinking    # final round: serial, cold prefix cache before every run (cache reset,
                                 # periodic restarts; CBENCH_RESTART_* in .env), --rerun to redo recorded configs
cbench rescore                   # protocol, visibility, repo-tests fields from saved runs, no model calls
cbench grade                     # blind judge per task (uses the Claude account; --task to limit)
cbench report
```
Model under test: Qwen 3.8 27B. Results so far come from the FP8 build on vLLM (`[models."qwen3.8-27b-fp8"]`,
2x B60, TP=2, 4 concurrent requests): pass `--model qwen3.8-27b-fp8`, since the default entry is the
llama.cpp GGUF (`Qwen3.8-27B-UD-Q6_K_XL.gguf`, KV cache q8_0). Embedder: Ollama on the iGPU, port 21434 (`embeddinggemma-2`).
