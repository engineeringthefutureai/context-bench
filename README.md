# context-bench

Measuring how context building shapes LLM-written code, from one pasted file to agentic search.

The same coding tasks run on the same model eight times, each with a different way of building the
model's context:

| Level | Strategy |
|---|---|
| L0 | No context |
| L1 | One file |
| L2 | Hand-picked files |
| L3 | Whole project |
| L4 | File-level RAG |
| L5 | Function-level RAG |
| L6 | Repo map |
| L7 | Agentic search (grep / hybrid) |

Deprioritized: a symbol-graph level (definitions of referenced symbols + 1-hop callers).

Diagram of the ladder and what each level tests: [docs/diagrams/context-levels.html](docs/diagrams/context-levels.html)
(source: `docs/diagrams/context-levels.json`, rendered with archify).

For every run we record whether the hidden tests pass, whether the model reused code the repo already
has or wrote its own version, whether it referenced things that don't exist, and what the run cost in
tokens and time.

Each level lives in its own self-contained module under `context_bench/levels/`, so every strategy can
be read on its own.

**Status:** skeleton. Interfaces and specs are in place; implementations land level by level.

```bash
pip install -e '.[dev]'
pytest
cbench --help
```

Design notes: `docs/design.md`.
