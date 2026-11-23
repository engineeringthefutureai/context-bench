# Repo L: design (draft for review)

Status: proposal, 2026-11-22. Nothing under `fixtures/repo_L/` exists yet, and no config refers to it.
Repos S and M stay frozen.

## Why
Repo M (~40k tokens, files of at most 101 lines) fits the whole-project level (L3) comfortably and puts
no pressure on an agent's context: L7 runs peak at 6-7k of a 98k window, and reading a whole file
costs 300-500 tokens. Selective reading (`rich` / `selective`: read_symbol, ranged reads, drop) has
nothing to save. Repo L should make context a real constraint, the way it is in a production codebase:
- L3 no longer fits (NO FIT), and L6's repo map gets heavy.
- Some code sits inside very long functions and files, so reading everything is expensive and skimming
  is wrong.

## What it is
`shopkit` about a year after M: the same company and code base, grown and aged. A snapshot, not a
strict superset:
- Most of M is still there, unchanged or lightly edited. Some modules were refactored, some grew,
  and a few gained heavy instrumentation during production incidents and were never cleaned up.
- Every existing task stays valid on L: its target file exists and doesn't already contain the feature,
  and the public API of every helper a task needs is unchanged (bodies may differ). Where M's
  `reference.diff` doesn't apply, the task gets `solutions/reference_L.diff` (and
  `context_blind_L.diff` / `trusting_L.diff` as needed). `test_tasks_verified` checks every task on L.
- M -> L is therefore size *and* age, not a pure size axis. The report says so.

## Size and shape
Target ~250k tokens of real code (L3 prompt ~2.5x the window: NO FIT), ~300-400 files, own passing
`tests/`. The bulk is function bodies, not declarations: the L6 map (signatures) should be ~20-25% of
the code's tokens (~55k, fits) and L6+docs ~30-35% (~80-90k, on the edge of the 98k window). Check
after each package: tokens of bodies vs signatures, and the L6 / L6+docs map sizes.

| Kind | Share of code | What it looks like |
|---|---|---|
| Current-style modules | ~60% | 100-400 lines, dataclasses, type hints, integer cents, domain errors, like M today |
| Grown modules | ~15% | 600-1,200 lines: new functions added to old files, mixed style inside one file |
| Legacy monoliths | ~15% | 3-4 files of 1,500-3,000 lines: an old order engine, a reporting monolith, an ERP/warehouse sync, a pricing module. camelCase, float money, module globals, broad `except`, long functions next to short ones |
| Dead code | ~10% | whole unused modules, unused functions inside live files, deprecated wrappers that only forward to the new API, a half-finished migration (`v2` package next to the old one). Named like the canonical place, never labelled dead. Only their own tests use them |

### Incident-bloated functions (the selective-reading case)
4-6 functions of ~400-600 lines each, in live code paths (checkout pricing, refund settlement,
inventory reservation, payout batching, webhook delivery). Each is a reasonable 60-120-line function
wrapped in leftovers of production troubleshooting:
- timers and debug logging around every step (`log.debug("INC-2291 step 4b ...")`), temporary counters,
  `if settings.TRACE_REFUNDS:` blocks, dumps of intermediate state
- commented-out older versions of a block, `# TEMP until INC-2291 is closed`, a retry loop added
  during an outage, a feature flag that is permanently on in production
- duplicated validation (old and new kept "to compare"), a shadow computation whose result is only
  logged

Somewhere in the middle (not the first or last 40 lines) each holds one piece of real business
behaviour that tasks depend on: a rounding rule, an early return for a customer tier, a currency
special case, a side effect (an event, a ledger entry). It's easy to miss when skimming, and
`semantic_search` hits (40 lines) won't show it.

## Layers (full stack)
Tasks cross layers, and every layer must be testable from Python with the standard library:
- **SQL migrations** (`migrations/NNNN_*.sql`), applied with `sqlite3` by the repo's own migration
  runner; tests check the resulting schema and data. Legacy part: a few hand-written ad-hoc scripts.
- **Python**: models, services, API handlers and serializers (as in M), plus the legacy monoliths.
- **Server-rendered HTML templates** (`templates/**/*.html`) rendered by the repo's small template
  engine; tests render pages and check the output (a new field on the order page, a label, escaping).
- **Config** (TOML/JSON) and the API's JSON contract.
No JS frontend: it would need Node to test, and the guide promises Python plus an API.
Harness consequence: the Python-aware tools (L5 function chunks, L6 map, L7 read_symbol and
enclosing-function hints) don't see SQL or templates as symbols; those files are visible at L3, L4
(whole files), grep and read_file. Accepted and reported, not patched per format.

## Planted helpers, decoys, conventions
- Keep M's 25 helpers (same public API). Add ~10 L helpers, about half inside long files or legacy
  modules, some only reachable by reading a bloated function (it calls a private helper whose body
  holds the rule).
- Decoys: near-miss copies in legacy modules and in the `v2` migration, each with tests, failing the
  hidden tests if used instead (as in M).
- Conventions differ by area: new code raises domain errors and publishes events; legacy code returns
  error codes. A task in new code must follow the new convention even though the nearest examples
  are legacy.
- `helpers/registry.toml`: L helpers, decoys and `[legacy]` paths, marked L-only (S/M analysis
  unchanged).

## Tasks (L-only, 8-12)
- **Long-function behaviour (3-4):** change or extend behaviour that lives inside a bloated function.
  The hidden tests check the buried rule is kept (or applied to the new case).
- **Helper in a long file (2-3):** reuse a helper defined in a 1,500+-line module (opaque name).
- **Cross-file through legacy (2):** a feature touching a new module and a legacy monolith, in each
  one's own convention.
- **Trap (1):** a documented helper in the migration package whose body drifted.
- **Full stack (2-3):** migration -> model -> service -> API serializer -> template, each layer in
  its own convention; the hidden tests check the end result through the API and the rendered page.
- **Control (1):** all info in the target file, which is itself long (tests that a long target alone
  doesn't break small-context levels).
- Every task: unmodified fails, reference passes and keeps `tests/` green, context-blind fails, as now.

## How it gets built
1. This design reviewed; module map with target sizes per file (a table in this doc).
2. Copy M to `fixtures/repo_L/`, then evolve package by package: edits, growth, the legacy monoliths,
   the bloated functions, dead code. Tests are written alongside and must pass at every step.
3. Size check: tokens per file and total (`cbench`'s tokenizer count); L3 must exceed the window.
4. Existing tasks re-verified on L (per-repo solutions where needed), then the L tasks with their
   hidden tests and solutions.
5. `validate-retrieval` on L (recall of planted helpers per retriever) before any sweep.
6. Only then config: `repos.L`, the levels to run on L (L3 shows NO FIT; L0-L2 stay on the smallest
   repo), and a short pilot (L7 grep/hybrid/rich/selective, thinking off) before the full round.

Several sessions of work. The current round (M) finishes first and is reported on its own.

## Harness changes for the L round
- `selective` v2: past a context threshold (e.g. 50% of the window) any earlier result can be dropped,
  not only the live tail; the cache cost is measured as now. Introduced with repo L, so the M round's
  `selective` stays exactly as tested.

## Decisions (2026-11-22)
- Size: ~250k tokens of real code; L3 NO FIT, L6 fits, L6+docs on the edge. No capping of L6.
- Full stack with SQL migrations and server-rendered templates, tested from Python; no JS.
- A snapshot a year after M (not byte-identical); existing tasks re-verified on L.
