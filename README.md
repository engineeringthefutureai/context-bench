# context-bench

Measuring how context building shapes LLM-written code, from one pasted file to agentic search.

Site: **https://context.engineeringthefuture.ai/**, with the results, every run's raw data, the method, the
levels, the tasks and notable case studies.

One model, Qwen 3.8 27B, gets the same 30 coding tasks at every level. Only the context changes. System
prompt, task text, output format and generation settings stay identical. The levels are deliberately simple,
one harness component each, so the results show what even a crude context strategy does to the code.

| Level | Context |
|---|---|
| L0 | Task text only (control tasks) |
| L1 | The target file |
| L2 | The target, its imports and its test file |
| L3 | The whole repository |
| L4 | Target + 3 files found by BM25 or embeddings |
| L5 | Target + 8 functions found by search (task text or the model's own queries) |
| L6 | Target + a map of every signature (optionally with docstrings) |
| L7 | An agent with search tools and a 15-call budget, starting from the file tree: grep, hybrid, rich, selective |
| L8 | An agent that reads by symbol and edits the repository in place |
| L-AG | Baseline: Google's Antigravity agent harness, confined in Docker, with Gemini 3.5 Flash-Lite and with Qwen |

Each level lives in its own self-contained module under `context_bench/levels/`, readable without the others.

Every run records whether the hidden tests passed, whether the model reused the helper the repository already
has (a spy test checks this) or wrote its own, whether it had seen that helper, whether it touched dead code,
whether the repository's own tests still pass, and what the run cost in tokens and time. A blind judge then
reads every passing patch for defects the tests miss.

## Results (prompt v4 round)

2,122 runs on repos S, M and L, with thinking off and on, one sample per configuration. On these tasks:

- The agents (L7, L8) passed 93-96% with thinking on, on every repo size. The best search-based level reached 68%.
- Clean passes, which pass the tests, keep the repository's own tests green and draw no penalty from the judge,
  rose from 10% with one file to 82% at L8.
- Thinking lifted the one-shot levels L1-L6 from 35% to 59% PASS, and the agents from 81% to 93%.
- With thinking on, the agents read about half as much, and the whole round used fewer tokens (53M against 33M).

The full write-up, the hypotheses and their verdicts are on the site. The v3 round (repos S and M, 880 runs)
stays available at the tag `round-v3`.

## A commercial harness as the baseline

L-AG is the baseline of what a complete agent harness achieves on these tasks: Google's Antigravity, a
commercial product with many thousands of hours of development behind it, against primitive harnesses put
together in a few days for this experiment. It was paired with Gemini 3.5 Flash-Lite, one of the cheapest
Gemini models, since these 30 tasks are a simple suite, well below where frontier models are benchmarked today.

| Harness + model | PASS | Shell commands that ran nothing | Median input per run |
|---|---|---|---|
| Antigravity + Gemini 3.5 Flash-Lite | 54/57 | 0 of 823 | 452k |
| Antigravity + Qwen 3.8 27B, thinking on | 53/57 | 408 of 804 | 301k |
| L8 + Qwen 3.8 27B, thinking on | 55/57 | – | 103k |
| L7 selective + Qwen 3.8 27B, thinking on | 54/57 | – | 48k |

Antigravity and Gemini showed a high synergy: not one empty command, though the pair didn't reach every task
(it missed reuse-010@M, long-004@L and reuse-008@L). Qwen in the same harness often named tool arguments in
its own style, which Antigravity ignores, so many of its commands ran nothing. Meanwhile the primitive
harnesses with a mid-size local model produced competitive results. Wall-clock times aren't comparable:
Gemini ran on Google's hosted API, six at a time, and Qwen on two local GPUs, two at a time.

## Tasks and repos

30 tasks on one synthetic shop codebase (`shopkit`) in three sizes:

- **S** (~2k tokens) and **M** (~41k tokens, 25 planted helpers, near-miss decoys, unlabelled dead code)
- **L** (~169k tokens, too big for the model's window at L3): the same shop a year later, with SQL
  migrations, templates and incident-bloated functions

By category: 13 reuse, 4 cross-file, 3 convention, 4 control, 5 long-function and 1 trap, whose helper's
docstring no longer matches its body. Hidden tests live outside the repository and never enter the context.
Every task has a reference solution that passes, and the context-dependent ones keep a plausible solution
written without the repository that fails.

## How the harness works

- **Outcomes** come from the hidden tests alone: PASS, FAIL, UNPARSABLE, ASKED TO EXPLORE, NO ANSWER,
  STOPPED EARLY, INVALID, OUT OF TOKENS, OUT OF TIME, NO FIT (`docs/labeling-design.md`).
- **Second chance:** when an agent tries to finish, the harness compiles every changed file (L7) or runs the
  repository's own tests (L8), never the hidden ones, and returns a failure for one more turn. The agent isn't
  told in advance.
- **Grading:** a blind judge (Gemini 4 Neon in `agy`) penalises defects in passing patches from a fixed catalogue and must quote
  a changed line for each (`docs/penalty-grading-design.md`). It never changes PASS or FAIL.
- **L-AG:** each run gets its own Docker compose project. The agent's container sees only its repository copy,
  and a separate proxy container holds the model API key (`docker/l-ag/`). The model is replaceable: Qwen on
  vLLM, or Gemini through its API.

## Running it

```bash
pip install -e '.[dev]'
pytest
cp .env.example .env                 # model server key; GEMINI_API_KEY for L-AG on Gemini
cbench validate-retrieval --retrievers bm25,embed
cbench one --task reuse-001 --level L1
cbench run --dry-run                 # what a sweep would run; `cbench run` resumes by run id
cbench run --thinking                # every configuration with thinking on
cbench grade --mode penalty --judge agy --export DIR   # blind grading queue for the agy judge
cbench report
```

Any OpenAI-compatible endpoint works, plus an embedder for the embedding variants. The guide page covers
configuration: models, the embedder, generation and the sweep, and how long a round takes.

## The site

`docs/` is published with GitHub Pages at context.engineeringthefuture.ai. One layout, `site/layout.html`
(head, top bar, footer), wraps two kinds of content:

- **Written pages**, `site/pages/*.md` (method, levels, case studies, guide, downloads) and `*.html` shells
  (raw data, tasks, 404): `python3 scripts/build_pages.py` renders them (needs `pip install -e '.[site]'`).
- **Generated content**: `scripts/build_report.py` builds the Results page from `results/`, and
  `scripts/build_site.py` writes the run data and task JSON that the raw-data viewer and the tasks page load.
  It scrubs local paths and ends with a leak scan.

`scripts/live_site.py` serves a live preview that re-renders on every request. Assets are versioned by
content. Visits are counted with Google Analytics (`docs/analytics.js`, off on local previews).

## Design notes

- `docs/design.md`: questions, metrics, controls and decisions
- `docs/semantic-search-design.md`: the embedding index (no truncation, windows, bulk builds)
- `docs/repo-L-design.md`: how repo L was grown from M
- `docs/penalty-grading-design.md`: the penalty judge
- `docs/labeling-design.md`: how runs get outcomes and badges
- `CLAUDE.md`: the hard rules and the current state of every level, in detail

## Credits

The harness, the analysis and the site were written with [Claude Code](https://claude.com/claude-code)
(Claude Opus 5.5). The model under study is Qwen 3.8 27B, served by vLLM, with EmbeddingGemma 2 on Ollama for
embeddings. Gemini 4 Neon in Google Antigravity (`agy`) is the blind judge, and the Antigravity SDK powers the L-AG reference.

## License

MIT, see [LICENSE](LICENSE). It covers the code, tasks, fixtures, rubric and published run data.
Dependencies keep their own permissive licenses (httpx and Markdown: BSD-3-Clause; numpy: BSD; pytest: MIT;
the Antigravity SDK, installed only in the `docker/l-ag` image: Apache-2.0). The site loads highlight.js
(BSD-3-Clause) and its fonts (SIL Open Font License) from their CDNs.
