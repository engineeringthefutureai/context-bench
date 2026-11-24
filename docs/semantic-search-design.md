# Semantic search (design, 2026-11-23)

How the embedding retriever is built and why, what went wrong on the way, and what is still open.
The retriever serves L4 `embed` (whole files), L5 `embed` (functions, `task_vs_code` and
`model_queries`), and the agents' `semantic_search` (L7 hybrid, rich, selective, and L8; functions).
Badges also use the embedder, for sentence classification only (`context_bench/badges.py`); they never
touch the index.

Code: `context_bench/embedders.py` (clients, cache), `context_bench/retrieval/dense.py` (index, windows,
search), `context_bench/bulk_index.py` (bulk build on the B60s), `Experiment.build_indexes`
(`runner.py`). Settings: `[embedder]` and `[bulk_index]` in `config/experiment.toml`.

## Decisions at a glance

| Question | Decision |
|---|---|
| Embedder | EmbeddingGemma 2 (`embeddinggemma-2:latest`), from the v4 rerun on |
| Window | 8,192 tokens per input, set per request |
| Truncation | Never. Too-long input is an error, not a silent cut |
| Functions and classes | Embedded whole, never split. One that doesn't fit stops the build and is named |
| Whole files | Line windows of about 4k tokens (10,000 chars) with 1,000 chars of overlap |
| A file's score | Its best window |
| Where documents are embedded | Ahead of the sweep (pre-flight), or in bulk on both B60s |
| Where queries are embedded | Ollama on the iGPU, every run, never cached |

## Embedder choice

Two small embedders were compared on repo M with `cbench validate-retrieval` (query = task text,
target file left out, recall of the planted helpers):

| | function recall@8 | file recall@8 |
|---|---|---|
| qwen3-embedding:0.6b | 8/12 | 8/12 |
| EmbeddingGemma 2 | 7/12 | 9/12 |

A wash on this set, so the choice was made on other grounds: the user moved to Gemma on 2026-11-23
(an 8k window, a documented task format, and the same model then serves the badge classifier). The v4 rounds up to 2026-11-23 used
Qwen3 (archived in `results/archive-v4-qwen3emb/`). Everything from the Gemma rerun on uses Gemma, and the
two are never mixed in one comparison.

**Prefixes.** EmbeddingGemma expects a task format on both sides: queries get
`task: code retrieval | query: `, documents `title: none | text: `. Qwen3 took an instruction on queries
only. The prefix is part of the cached text, so changing it re-embeds everything.

## The truncation we didn't know about

This is the finding that forced most of this doc. Both embedders were cutting long inputs off without a
word:

- **Gemma behind Ollama's OpenAI-style endpoint stopped at 2,048 tokens.** Ollama's default batch size
  (`num_batch`) is 2,048, and the `/v1/embeddings` endpoint truncates to fit and can't be told not to.
  Raising only `num_ctx` doesn't help. The batch has to match the window.
- **Qwen3 through the same endpoint stopped at 4,095 tokens.** The archived v4 runs with file-level
  retrieval on repo L saw truncated file vectors. S and M were unaffected: their largest file is 3,200
  characters, well under either limit.

Hard rule 3 says context is never truncated silently. An embedding that sees only a file's first 2k
tokens is the same failure one step removed: the retriever ranks files by their first page and nobody can
tell. So:

1. The client uses Ollama's own `/api/embed` with `truncate: false`. An input longer than the window comes
   back as HTTP 400, which the client raises as an error.
2. Each request sets `num_ctx` and `num_batch` together (up to `context_tokens = 8192`).
3. Verified: the native and OpenAI endpoints give identical vectors for short texts (cosine 1.0), and the
   8k options don't change short-text vectors. So the S and M runs already made with Gemma stand. Only
   the 120 repo L runs that used embeddings were set aside (`results/superseded/L-embed-6k-windows.jsonl`).

## Long inputs: functions whole, files in windows

**Functions and classes are never split.** A function is the unit L5 shows and `semantic_search`
returns. Half a function embedded on its own would rank on half its meaning, and the model would be
handed a hit that isn't what scored. If one doesn't fit the window, the build stops with
`TooLongForEmbedder` naming it, and the decision goes to a person (rename, raise the window, or accept a
miss). Measured on repo L, the largest five all fit:

| Function | chars | tokens |
|---|---|---|
| `payouts.batching.build_payout_batch` | 16,760 | 4,826 |
| `core.order_engine.OrderEngine` | 15,901 | 4,261 |
| `api.webhook_delivery.deliver_pending` | 12,591 | 3,583 |
| `pricing.cart.price_cart` | 12,399 | 3,424 |
| `payments.settlement.settle_refund` | 12,179 | 3,520 |

The rest of repo L's 1,352 functions are smaller, and S and M top out at 2,829 characters.

**Whole files are split into windows.** A file is a bag of unrelated functions, and its meaning really
does change from top to bottom, so windows lose little. Files over `max_chars` (10,000 characters, about
4k tokens of code) become line windows that step back about `overlap_chars` (1,000) at each boundary, so
a function cut by a boundary appears whole in at least one window when it's short. A window the embedder
still refuses (dense code runs nearer 2.5 characters per token) is halved by lines and tried again. On
repo L that's 9 of 278 files, 21 windows. S and M have none.

Why 4k windows in an 8k window: the user's call ("6k of text is pathetic", then "the sliding window
doesn't have to be 8k, we probably should do 4k"). Smaller windows keep a file's vector about one region
of code, which is what makes "best window" meaningful. Functions use the full 8k because they can't be
split.

**A file scores as its best window** (`np.maximum.at` over the window scores). Simple, and it matches
what a reader does: one relevant region makes the file relevant.

## Trade-offs we accepted

- **Best-window scoring favours long files.** A file with 4 windows gets 4 chances to match. On repo L only
  9 files have more than one window, so the bias is small. Averaging or a length penalty would be the
  fix if it ever shows up in the results.
- **A window doesn't know which file it came from** beyond its first line. `file_text` starts with the
  path, so window 1 carries it, and later windows don't. Open idea, not done: repeat the path (and the
  file's import block) at the top of every window. It costs a re-embed and is a change of method, so it
  waits for a reason.
- **Small, short-context embedder versus a long-context one.** An embedder with a 32k window could take
  whole files and the largest functions in one piece. We chose not to: one vector for 30k characters of
  unrelated functions is a blurred average (dilution), the long-context models we could serve on this
  box are larger and slower on the iGPU, and windows give each region its own vector. The cost is the
  windowing code and the long-file bias above.
- **Documents are embedded ahead of time, queries live.** That's how RAG is deployed, and it keeps the
  per-run cost honest: a query always goes to the iGPU (40-200 ms) and is never served from the cache.
  The document vectors are cached on disk per model and text (`.cache/embeddings/<model>/`), so a run
  never waits for indexing.

## Serving, batching, and the crashes

The query embedder is Ollama on the iGPU (Vulkan, port 21434, profile `ollama-igpu` in the GPU manager).
It is slow for bulk work and fragile at an 8k window:

- **A batch of 32 whole repo L files crashed it** (about 65k tokens in one request). Requests are now
  capped at 32 texts and 24,000 characters.
- **At an 8k window it still crashed**, after about 4 minutes, at both 48k and 24k characters per
  request. Single inputs and batches of 2 or 4 near-limit windows went through. The working theory is
  that Ollama reserves the window per input, so many short texts at 8k cost more than a few long ones.
  The client now sorts texts by length and gives each request the smallest window that holds its longest
  text (2,048, 4,096 or 8,192), with at most 4 inputs once the window is above 2,048. Not yet confirmed on
  a full repo L build. That's what the bulk builder below is for.
- **A pre-flight before every sweep** builds the indexes for the repos that need embeddings, so a too-long
  function or a dead embedder stops the sweep before its first run, not in the middle of it.

## Bulk index builder on the two B60s

Re-indexing is not a one-off: changing the embedder, the prefixes, or the window size re-embeds
everything, and repo L is most of the work. `cbench index --embeddings --bulk [--repo-size L]` builds on
both B60s instead of the iGPU:

1. Through the GPU manager (aries-modeld), it loads the `[bulk_index]` profiles: one stock
   `ollama/ollama` container per card, same model store as the iGPU server, Vulkan. Loading them evicts
   vLLM, which holds both cards.
2. It runs the normal index build (same texts, windows, prefixes, no truncation) with a pool embedder
   that deals texts out to both servers, longest first, in jobs of 16. Vectors are saved as each job
   finishes, so a failure late in the pass loses only the jobs in flight. A dropped connection is retried
   three times (after 10, 30 and 90 s).
3. It unloads the bulk profiles and loads again what they evicted.
4. It re-embeds a sample (short functions and long file windows) on the iGPU and reports the lowest
   cosine against the bulk vectors. Documents come from a B60 and queries from the iGPU, so this
   checks they agree. Below 0.999 it prints a warning.

**Why through the manager and not `docker run`.** The manager's agent reads every GPU process's memory use
from `/proc/<pid>/fdinfo`. Since 0.2.5 it pauses those reads while a container it manages is stopping. A
container it doesn't manage stopping on a B60 would race those reads in the xe driver, the bug behind the
three host lockups. Running the builders as manager profiles keeps every stop under that pause.

**Why Ollama and not sentence-transformers.** The same server and model files as the query embedder, so
the only difference between document and query vectors is the GPU, and the sample check measures that.
A PyTorch XPU stack would be a second implementation of the model to keep in agreement.

**Setup (once).** The profiles `ollama-b60-1` and `ollama-b60-2` live with the manager's other profiles
(`apps/ai/modeld/profiles.yaml` in the cluster repo, rolled out by Flux). They mirror `ollama-igpu`: the
same image and Ollama store, Vulkan, `gpu: { device: b60-N, vram: 6GiB }`, ports 11435 and 11436 (the
agent's counting proxy, Ollama itself on 21435 and 21436), `idle_unload: 30m` as a safety net if a
build dies before unloading them.

The tool also needs, in `.env` (gitignored): `ARIES_MANAGER_URL=<manager>/api/v1` and `ARIES_CLIENT_TOKEN=<token>`. The
config holds only profile names and local ports, never the manager's address or token.

## Open

- Confirm the length-sized batching on a full repo L build (bulk on the B60s first, then a spot check on
  the iGPU).
- The repo L embedding runs (120, set aside) run again once the index is built.
- Repeat the file path and imports in every window: noted above, waits for a reason.
- Best-window length bias: watch L4 `embed` on repo L for long files ranking first on unrelated tasks.
