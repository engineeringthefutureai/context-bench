# context-bench

Measuring how context building shapes LLM-written code, from one pasted file to agentic search.

Site: **https://context.engineeringthefuture.ai/** (results, the raw data of every run, method, tasks).

The same model gets the same coding tasks nine times over, each time with its context built a different way,
while the system prompt, task text, output format and generation settings stay identical at every level. The levels are deliberately simple, one harness component each, so the results show what even a crude
context strategy does to the code.

| Level | Strategy |
|---|---|
| L0 | No context (control tasks only) |
| L1 | One file (the target) |
| L2 | Hand-picked files: the target, its imports, its test file |
| L3 | Whole project |
| L4 | File-level RAG (BM25 or embeddings) |
| L5 | Function-level RAG (task text or the model's own queries) |
| L6 | Repo map: one-line signatures (+ docstrings) |
| L7 | Agentic search: grep / hybrid / rich / selective tool sets, 15-call budget |
| L8 | Experimental agent: symbol list, `read_symbol` only, edits made with `edit` / `create_file` tools |

Each level sits in its own self-contained module under `context_bench/levels/`, readable without the others.

Every run records whether the hidden tests passed, whether the model reused the helper the repo already has
(a spy test checks this) or wrote its own, whether it had seen that helper at all, whether it touched dead code,
whether the repo's own tests still pass, and what the run cost in tokens and time.

## Tasks and repos

30 tasks on one synthetic shop codebase (`shopkit`) in three sizes:

- **S** (~2k tokens) and **M** (~41k tokens, 25 planted helpers, near-miss decoys, unlabelled dead code)
- **L** (~169k tokens, too big for the model's window at L3): the same shop a year later, with SQL
  migrations, templates and incident-bloated functions

By category that's 13 reuse, 4 cross-file, 3 convention, 4 control, 5 long-function and 1 trap, a helper whose
docstring no longer matches its body. Hidden tests live outside the repo and never enter the context.

## Status

- **Prompt v3 round (complete, tag `round-v3`):** repos S and M, levels L0-L7, thinking off and on (clean
  mode: cold prefix cache before every run), 440 + 440 runs, blind-graded. Findings:
  [docs/findings.md](docs/findings.md), illustrated in [docs/report.html](docs/report.html). The diagrams in
  `docs/diagrams/` (levels, task coverage, experiment flow) describe this round.
- **Prompt v4 round (in progress, `main`):** adds repo L, L8, the file tree as L7's starting context, and
  EmbeddingGemma 2 with an 8k window and no truncation. Thinking off first, then the clean thinking-on
  round. Penalty grading comes after that.

The site (`docs/`, published with GitHub Pages at context.engineeringthefuture.ai) has one layout,
`site/layout.html` (head, top bar, footer), and two kinds of content:

- **Written pages**, `site/pages/*.md` (method, guide, downloads) and `*.html` shells (raw data, tasks, 404):
  `python3 scripts/build_pages.py` renders them into the layout (needs `pip install -e '.[site]'`).
- **Generated content**: `scripts/build_report.py` builds the Results page from `results/` into the same
  layout; `scripts/build_site.py` writes the run data and tasks JSON that the raw-data and tasks pages load
  on demand.

`scripts/live_site.py` serves a live preview that re-renders everything on every request. Assets are versioned
by content, so nothing goes stale. Visits are counted with Google Analytics
(`docs/analytics.js`, off on local previews).

## Running it

```bash
pip install -e '.[dev]'
pytest
cp .env.example .env                 # API key, optional restart command for clean mode
cbench validate-retrieval --retrievers bm25,embed
cbench one --task reuse-001 --level L1
cbench run --dry-run                 # what a sweep would run; `cbench run` resumes by run id
cbench report
```

The guide page (`docs/guide.html`) covers configuration: models, the embedder, generation and the sweep.

## Design notes

- `docs/design.md`: questions, metrics, controls, decisions, and dated results per experiment
- `docs/semantic-search-design.md`: the embedding index (no truncation, windows, bulk builds on the B60s)
- `docs/repo-L-design.md`: how repo L was grown from M
- `docs/penalty-grading-design.md`: the judge for the v4 round
- `docs/labeling-design.md`: how runs get outcomes and badges (exact rules, embeddings, validation)
- `CLAUDE.md`: the hard rules and the current state of every level, in detail
