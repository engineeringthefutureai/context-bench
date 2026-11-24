<!--
title: Context Bench · Guide: Running Context Bench
description: What is needed to run context-bench: Python and an OpenAI-compatible API; an embedder, cache control and a judge are optional.
eyebrow: Guide
heading: Running the experiment
lede: Only Python and an OpenAI-compatible chat endpoint are needed for the harness. Self-hosting is optional.
-->

## What is needed

<div class="table-wrap"><table>
  <thead><tr><th>Component</th><th>Needed?</th><th>What it is used for</th></tr></thead>
  <tbody>
    <tr><td>Python 3.11+</td><td>Yes</td><td>The harness, the fixture repository and the tests are run with it. The standard library and a few packages are used.</td></tr>
    <tr><td>Chat endpoint</td><td>Yes</td><td>Any OpenAI-compatible <code>/v1/chat/completions</code> API with tool calling (needed for L7). A hosted API can be used.</td></tr>
    <tr><td>Embeddings endpoint</td><td>Optional</td><td>Any <code>/v1/embeddings</code> API, used for the embedding retriever (L4/L5 <code>embed</code>, L7 <code>hybrid</code>). Without it, those variants are skipped and BM25 is still run.</td></tr>
    <tr><td>Cache control</td><td>Optional</td><td>For clean mode, a vLLM server with <code>VLLM_SERVER_DEV_MODE=1</code> (prefix-cache reset) or a restart command is needed. On a hosted API, caching can't be controlled, so cached prefixes may be shared between runs. Latency and cached-token counts are changed by this, not the answers' correctness.</td></tr>
    <tr><td>Judge</td><td>Optional</td><td>A coding-agent CLI used for blind grading (agy or Claude Code), confined to read-only tools. The test-based outcomes are never changed by it.</td></tr>
  </tbody>
</table></div>

## Steps

<pre class="block">git clone https://github.com/engineeringthefutureai/context-bench
cd context-bench
pip install -e '.[dev]' &amp;&amp; pytest             # every task's contract is checked too

cp .env.example .env                           # the endpoint URL and API key are set here
$EDITOR config/experiment.toml                 # [models], [embedder], [grading]

cbench validate-retrieval --retrievers bm25,embed
cbench one --task reuse-001 --level L1         # a single run
cbench run --model &lt;key&gt;                       # the full sweep, resumable
cbench run --model &lt;key&gt; --clean --thinking    # clean mode (cost, latency): serial, cold cache
cbench rescore                                 # derived fields are recomputed, no model calls
cbench grade --mode penalty --judge claude     # optional: each passing patch is graded by a penalty judge
cbench report</pre>

## Walk-through: a hosted model

Everything that has to be changed is kept in two files: `.env` for secrets and the server commands, `config/experiment.toml` for the model, the embedder and the sweep.

**1. The API key.** `.env.example` is copied to `.env`, and `CBENCH_API_KEY` is set in it. The key is sent as a bearer token and is never written to results or logs. The `CBENCH_RESTART_*` lines are left empty: they are meant for a self-run server.

**2. The model.** An entry is added under `[models]`. `base_url` is everything before `/v1`: `/v1/chat/completions` is appended by the harness. What counts as NO FIT is decided by `context_window`, so it should be set to what is really accepted by the endpoint. When the same URL is listed several times in `base_urls`, that many requests are run at once.

<pre class="block">[models."my-model"]
base_url = "https://api.example.com"     # /v1/chat/completions is appended by the harness
base_urls = ["https://api.example.com", "https://api.example.com"]   # 2 requests are sent at a time
model = "vendor/model-name"              # the id expected by the endpoint
quant = "as served by the provider"      # recorded with every run
context_window = 131072</pre>

**3. The embedder (optional).** Any `/v1/embeddings` endpoint and model can be given in `[embedder]`. With Ollama, its own endpoint is used with `api = "ollama"`, so an input that is too long is reported as an error instead of being cut off (`context_tokens` should be set to the model's window). Document vectors are cached on disk per text, and every file and function is embedded by the sweep before its first run (this is also done on its own by `cbench index --embeddings`). Without an embedder, the configs that use it (`retriever = "embed"`, and the agents with `semantic_search`: hybrid, rich, selective, L8) are skipped with `--level`, or their `[[config]]` blocks are removed.

**4. Generation.** `max_output_tokens` (thinking is counted toward it) and `thinking` are set in `[generation]`. The temperature is left at the server's default unless one is set. The same values are given to every level.

**5. The sweep.** One configuration is defined by each `[[config]]` block: a level plus its options (retriever, query mode, agent variant, prompt arm), optionally limited to some tasks or repos. What would be run is listed by `cbench run --dry-run`. The sweep is resumable: every recorded configuration is skipped on a rerun, and a run that hit a server error is simply retried the next time.

**6. Check, run, read.** `cbench validate-retrieval` is run first (the recall of the planted helpers, by which it is shown whether the embedder works), then `cbench one` on one task, then `cbench run`, and finally `cbench report` or the site builders in `scripts/`.

## How long it takes

The figures are taken from the prompt-v3 round (repos S and M, 440 runs per round, Qwen 3.8 27B on two Intel B60 cards). Token totals are carried over to any endpoint; the time depends on the hardware or provider used.

<div class="table-wrap"><table>
  <thead><tr><th>Round</th><th class="r">Runs</th><th class="r">Input tokens</th><th class="r">Output tokens</th><th class="r">Model time</th></tr></thead>
  <tbody>
    <tr><td>Thinking off, 4 at a time</td><td class="r">440</td><td class="r">6.8M</td><td class="r">0.3M</td><td class="r">5.7 h (about 2 h wall clock)</td></tr>
    <tr><td>Thinking on, clean (serial, cold cache)</td><td class="r">440</td><td class="r">4.8M</td><td class="r">3.4M</td><td class="r">24.6 h, plus restarts</td></tr>
  </tbody>
</table></div>

Input is dominated by L3 (the whole repository, about 32k tokens a run) and L7 (27-46k over all its turns). Output is dominated by thinking: with it on, 5-13k tokens are written per run, against about 0.5-1k without. About 450 configurations are added by repo L (from the v4 round), and 100-250k input tokens can be read by an L7 run there.

## Adding a task

Each task is defined by `tasks/<id>/task.toml` plus `hidden_tests/` and `solutions/`. The hidden tests must be failed by the unmodified repository and passed by `reference.diff`, with the repository's own tests kept green. For tasks that depend on repository context, a plausible `context_blind.diff` that fails is also kept. All of it is checked by `tests/test_tasks_verified.py`.
