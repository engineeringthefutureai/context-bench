# Repo L: design (draft for review)

Status: built 2026-11-22 (milestones 1-5, commits fb1324c..). Not in `config/experiment.toml` yet. Measured:
169k tokens at L3 (model tokenizer), L6 map 26k, L6+docs 32k (map share 15%: bodies dominate, so the map is far from
the edge), 278 files, 243 own tests. The bloated functions came out at 200-270 lines, not 400-600.
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

## Naming of old and dead code (rule)
Old code keeps the names it always had, and new code gets new names. Nothing says what is old, dead
or still used: no `legacy/`, `v2/`, `old_`, "deprecated", "live parts" or "not adopted" in paths,
identifiers, docstrings or comments (ordinary history and quirk comments are fine). In L:
- the old monoliths: `shopkit/core/` (`order_engine.py`, `reporting.py`, `erp.py`, `pricing.py`), parts
  still called from current code (`orders/lookup.py`, `reports/fiscal.py`, `inventory/erp_import.py`)
- the abandoned migration: `shopkit/domain/` (money, ids, orders, text), dead, tests only
- forwarding helpers: `shopkit/utils/helpers.py`, dead, tests only
- hand-run SQL: `migrations/manual/`
Which paths are dead is recorded only in `helpers/registry.toml` `[legacy]` (harness-only).

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

## Module map (targets; lines of Python unless noted)
Totals: ~27k lines of source + ~8k lines of tests + ~1.5k lines of SQL/HTML. M today: 3.1k + 2.5k tests.

| Area | Files | Target lines | Kind |
|---|---|---|---|
| M packages, evolved (orders, catalog, customers, inventory, payments, invoices, shipping, tax, promotions, notifications, marketing, reports, api, utils) | ~100 | ~7,000 | mostly current style; some grown; task targets stay valid |
| `shopkit/db/` migration runner, connection, repositories (sqlite3) | 6 | 900 | current |
| `migrations/*.sql` | ~25 | 700 (SQL) | current, 3 legacy ad-hoc scripts |
| `shopkit/web/` template engine, views, forms; `templates/**/*.html` | 8 + ~25 | 1,300 + 800 (HTML) | current |
| `shopkit/pricing/` price lists, tiers, currency conversion | 7 | 1,200 | current |
| `shopkit/fulfillment/` waves, picking, carrier handoff | 8 | 1,400 | current, one grown file |
| `shopkit/payouts/` marketplace seller payouts | 6 | 1,100 | current + 1 bloated function |
| `shopkit/subscriptions/` billing cycles, proration, dunning | 6 | 1,100 | current |
| `shopkit/accounting/` ledger, journal entries, period close | 7 | 1,300 | current |
| `shopkit/integrations/` ERP client, tax provider, carrier APIs (offline stubs) | 8 | 1,400 | mixed |
| `shopkit/fraud/`, `shopkit/support/`, `shopkit/search/` | 12 | 2,000 | current |
| `shopkit/legacy/` order_engine (2,500), reporting (2,000), erp_sync (1,800), pricing_old (1,500) | 4 | 7,800 | legacy monoliths, partly live |
| dead code: `common/` (from M), `shopkit/v2/` half-finished migration, deprecated wrappers, unused modules | ~15 | 2,500 | dead, tests only |
| incident-bloated functions (inside the files above): checkout pricing, refund settlement, stock reservation, payout batching, webhook delivery | 5 functions | ~2,500 of the totals | live |
| `tests/` | ~120 | ~8,000 | pytest and old unittest style |

Milestones (each: L's tests pass, a commit):
1. Copy M; `db` + migrations; `web` + templates; L's test runner green.
2. Evolve M packages (growth, edits, mixed style) and add `pricing`, `accounting`, `subscriptions`.
3. Legacy monoliths and dead code (`legacy/`, `v2/`).
4. Remaining packages; the five bloated functions with their buried rules.
5. Size and shape check (tokens, map ratio), existing tasks re-verified on L, L tasks.

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

## Constraints from the existing tasks (L must not solve them)
Each M task must still be unsolved on L, so L must not contain:
- `shopkit/utils/units.py` (`parse_weight`), `shopkit/utils/durations.py` (`parse_duration`)
- on `Order`: `add_line`, `discount_percent`, `gift_message` (keep `orders/models.py` close to M)
- `Stock.reserve` (stock reservation in L lives in fulfillment wave allocation, not on `Stock`)
- transfer announcements (audit/events) or `transfers_by_week` in `inventory/transfers.py`
- a `return_window_days` setting or a restocking fee in `orders/returns.py`
- `Product.weight_g`, `Product.url_path`, `parcel_weight_g` in fulfillment
- `export_orders_csv`; `expected_arrival` in refunds; a bank-transfer reference in `payments/gateway.py`
- email validation in `marketing/newsletter.py`; card masking in `api/webhooks.py` (the bloated webhook
  delivery function must not mask either)
- `customer`/`points` in `checkout.quote()`; `by_email` in `reports/customers_report.py`
- a weight on shipping labels (`shipping/labels.py`); trap-001's `format_weight` keeps its drift
New code may use these modules but must not add these features. `test_tasks_verified` on L checks it.
