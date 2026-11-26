<!--
title: Context Bench · Guide: Running Context Bench
description: What it takes to run context-bench. Python and an OpenAI-compatible API are required, while an embedder, cache control and a judge are optional.
eyebrow: Guide
heading: Running the experiment
lede: Python and an OpenAI-compatible chat endpoint are all the harness needs, and self-hosting is optional.
-->

## What is needed

<div class="table-wrap"><table>
  <thead><tr><th>Component</th><th>Needed?</th><th>What it is used for</th></tr></thead>
  <tbody>
    <tr><td>Python 3.11+</td><td>Yes</td><td>Runs the harness, the fixture repository and the tests. Beyond the standard library, the harness pulls in only a few packages.</td></tr>
    <tr><td>Chat endpoint</td><td>Yes</td><td>Any OpenAI-compatible <code>/v1/chat/completions</code> API with tool calling, which L7 and L8 depend on, and a hosted API works fine.</td></tr>
    <tr><td>Embeddings endpoint</td><td>Optional</td><td>Any <code>/v1/embeddings</code> API, for the embedding retriever (L4/L5 <code>embed</code>, L7 <code>hybrid</code>). Without one, the harness skips those variants and still runs BM25.</td></tr>
    <tr><td>Cache control</td><td>Optional</td><td>Clean mode needs a vLLM server with <code>VLLM_SERVER_DEV_MODE=1</code> (for the prefix-cache reset) or a restart command. A hosted API gives the harness no say over caching, so runs may share cached prefixes, which shifts latency and cached-token counts but leaves correctness unchanged.</td></tr>
    <tr><td>Judge</td><td>Optional</td><td>A coding-agent CLI that grades blind (agy or Claude Code), locked to read-only tools, which never touches the test-based outcomes.</td></tr>
  </tbody>
</table></div>

## Steps

<pre class="block">git clone https://github.com/engineeringthefutureai/context-bench
cd context-bench
pip install -e '.[dev]' &amp;&amp; pytest             # the tests also check every task's contract

cp .env.example .env                           # the endpoint URL and API key go here
$EDITOR config/experiment.toml                 # [models], [embedder], [grading]

cbench validate-retrieval --retrievers bm25,embed
cbench one --task reuse-001 --level L1         # a single run
cbench run --model &lt;key&gt;                       # the full sweep, resumable
cbench run --model &lt;key&gt; --clean --thinking    # clean mode (cost, latency): serial, cold cache
cbench rescore                                 # the harness recomputes derived fields, no model calls
cbench grade --mode penalty --judge claude     # optional: a penalty judge grades each passing patch
cbench report</pre>

## Walk-through: a hosted model

Everything that needs changing lives in two files: secrets and server commands in `.env`, and the model, the embedder and the sweep in `config/experiment.toml`.

**1. The API key.** `.env.example` is copied to `.env` and `CBENCH_API_KEY` is filled in. The harness sends the key as a bearer token and never writes it to results or logs. The `CBENCH_RESTART_*` lines stay empty, since they only matter for a self-run server.

**2. The model.** A new entry goes under `[models]`, and its `base_url` is everything before `/v1`, because the harness appends `/v1/chat/completions` itself. `context_window` decides what counts as NO FIT, so it should match what the endpoint really accepts. When `base_urls` lists the same URL several times, the harness runs that many requests at once.

<pre class="block">[models."my-model"]
base_url = "https://api.example.com"     # the harness appends /v1/chat/completions
base_urls = ["https://api.example.com", "https://api.example.com"]   # the harness sends 2 requests at a time
model = "vendor/model-name"              # the id expected by the endpoint
quant = "as served by the provider"      # recorded with every run
context_window = 131072</pre>

**3. The embedder (optional).** `[embedder]` takes any `/v1/embeddings` endpoint and model, and with `api = "ollama"` the harness talks to Ollama's own endpoint instead, and Ollama then reports an over-long input as an error rather than cutting it off (`context_tokens` should equal the model's window). The harness caches document vectors on disk, one per text. Before its first run the sweep embeds every file and function, and `cbench index --embeddings` does the same thing on demand. With no embedder at all, the configs that need one (`retriever = "embed"`, plus every agent with `semantic_search`, which means hybrid, rich, selective and L8) are skipped with `--level`, or their `[[config]]` blocks come out.

**4. Generation.** `[generation]` holds `max_output_tokens`, which thinking counts against, and `thinking`. The harness leaves temperature at the server's default unless one is set, and every level gets identical values.

**5. The sweep.** Each `[[config]]` block is one configuration, a level plus its options (retriever, query mode, agent variant, prompt arm), optionally narrowed to some tasks or repos. `cbench run --dry-run` lists what would run. Sweeps can be resumed: a rerun skips every configuration already recorded and retries any run that hit a server error.

**6. Check, run, read.** `cbench validate-retrieval` comes first, since it measures recall of the planted helpers and is the quickest way to tell whether the embedder works. Next comes `cbench one` on a single task, then `cbench run`, and finally `cbench report` or the site builders in `scripts/`.

## How long it takes

These figures come from the prompt-v4 round on repos S, M and L, with Qwen 3.8 27B on two Intel B60 cards and two requests at a time. Token totals carry over to any endpoint, while wall-clock time depends on the hardware or the provider.

<div class="table-wrap"><table>
  <thead><tr><th>Round</th><th class="r">Runs</th><th class="r">Input tokens</th><th class="r">Output tokens</th><th class="r">Model time</th></tr></thead>
  <tbody>
    <tr><td>Thinking off</td><td class="r">1,061</td><td class="r">51.8M</td><td class="r">1.1M</td><td class="r">14 h</td></tr>
    <tr><td>Thinking on</td><td class="r">947</td><td class="r">23.9M</td><td class="r">9.1M</td><td class="r">77 h</td></tr>
    <tr><td>L-AG reference, both rounds</td><td class="r">114</td><td class="r">42.1M</td><td class="r">1.1M</td><td class="r">14 h</td></tr>
  </tbody>
</table></div>

Model time adds up each run's own duration, so with two runs at a time the wall clock is roughly half of it. Most input goes to the agents, which re-send their growing conversation with every request: on repo L, an L7 or L8 run read a median 134k input tokens with thinking off and 77k with it on, and up to 469k. With thinking on, the model writes most of its output as thinking, and runs at the one-shot levels produced a median of 7-16k output tokens each.

## Adding a task

A task is `tasks/<id>/task.toml` plus two folders, `hidden_tests/` and `solutions/`. The unmodified repository has to fail the hidden tests, and `reference.diff` has to pass them while keeping the repository's own tests green. Tasks that depend on repository context also carry a plausible `context_blind.diff` that fails, and `tests/test_tasks_verified.py` checks every one of these conditions.
