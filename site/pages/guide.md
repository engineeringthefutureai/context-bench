<!--
title: Context Bench · Guide: Run Context Bench Yourself
description: What you need to run context-bench: Python and an OpenAI-compatible API; an embedder, cache control and a judge are optional.
eyebrow: Guide
heading: Run it yourself
lede: The harness needs Python and an OpenAI-compatible chat endpoint. Self-hosting is optional.
-->

## What you need

<div class="table-wrap"><table>
  <thead><tr><th>Component</th><th>Needed?</th><th>What it's for</th></tr></thead>
  <tbody>
    <tr><td>Python 3.11+</td><td>Yes</td><td>The harness, the fixture repository and the tests. Standard library plus a few packages.</td></tr>
    <tr><td>Chat endpoint</td><td>Yes</td><td>Any OpenAI-compatible <code>/v1/chat/completions</code> API with tool calling (for L7). A hosted API works.</td></tr>
    <tr><td>Embeddings endpoint</td><td>Optional</td><td>Any <code>/v1/embeddings</code> API, for the embedding retriever (L4/L5 <code>embed</code>, L7 <code>hybrid</code>). Without it those variants are skipped and BM25 still runs.</td></tr>
    <tr><td>Cache control</td><td>Optional</td><td>Clean mode needs a vLLM server with <code>VLLM_SERVER_DEV_MODE=1</code> (prefix-cache reset) or a restart command. On a hosted API you can't control caching, so runs may share cached prefixes. This changes latency and cached-token counts, not the answers' correctness.</td></tr>
    <tr><td>Judge</td><td>Optional</td><td>A coding-agent CLI for blind grading (agy or Claude Code), confined to read-only tools. It never changes the test-based outcomes.</td></tr>
  </tbody>
</table></div>

## Steps

<pre class="block">git clone https://github.com/engineeringthefutureai/context-bench
cd context-bench
pip install -e '.[dev]' &amp;&amp; pytest             # checks every task's contract too

cp .env.example .env                           # endpoint URL and API key
$EDITOR config/experiment.toml                 # [models], [embedder], [grading]

cbench validate-retrieval --retrievers bm25,embed
cbench one --task reuse-001 --level L1         # a single run
cbench run --model &lt;key&gt;                       # the full sweep, resumable
cbench run --model &lt;key&gt; --clean --thinking    # clean mode (cost, latency): serial, cold cache
cbench rescore                                 # recompute derived fields, no model calls
cbench grade --mode penalty --judge claude     # optional: penalty judge per passing patch
cbench report</pre>

## Walk-through: a hosted model

Everything you change lives in two files: `.env` for secrets and the server commands, `config/experiment.toml` for the model, the embedder and the sweep.

**1. The API key.** Copy `.env.example` to `.env` and set `CBENCH_API_KEY`. It is sent as a bearer token and never written to results or logs. Leave the `CBENCH_RESTART_*` lines empty: they are for a server you run yourself.

**2. The model.** Add an entry under `[models]`. `base_url` is everything before `/v1`: the harness appends `/v1/chat/completions`. `context_window` decides what counts as NO FIT, so set it to what the endpoint really accepts. Listing the same URL several times in `base_urls` runs that many requests at once.

<pre class="block">[models."my-model"]
base_url = "https://api.example.com"     # the harness adds /v1/chat/completions
base_urls = ["https://api.example.com", "https://api.example.com"]   # 2 requests at a time
model = "vendor/model-name"              # the id the endpoint expects
quant = "as served by the provider"      # recorded with every run
context_window = 131072</pre>

**3. The embedder (optional).** `[embedder]` takes any `/v1/embeddings` endpoint and model; with Ollama, `api = "ollama"` uses its own endpoint so an input that's too long is an error instead of being cut off (set `context_tokens` to the model's window). Document vectors are cached on disk per text, and the sweep embeds every file and function before its first run (`cbench index --embeddings` does it on its own). Without an embedder, skip the configs that use it (`retriever = "embed"`, and the agents with `semantic_search`: hybrid, rich, selective, L8) with `--level`, or remove their `[[config]]` blocks.

**4. Generation.** `[generation]` sets `max_output_tokens` (thinking counts toward it) and `thinking`. The temperature stays at the server's default unless you set one. Every level gets the same values.

**5. The sweep.** Each `[[config]]` block is one configuration: a level plus its options (retriever, query mode, agent variant, prompt arm), optionally limited to some tasks or repos. `cbench run --dry-run` lists what would run. The sweep resumes: rerunning skips every recorded configuration, and a run that hit a server error is simply retried next time.

**6. Check, run, read.** `cbench validate-retrieval` first (recall of the planted helpers, which tells you whether the embedder works), then `cbench one` on one task, then `cbench run`, then `cbench report` or the site builders in `scripts/`.

## How long it takes

From the prompt-v3 round (repos S and M, 440 runs per round, Qwen 3.8 27B on two Intel B60 cards). Token totals carry over to any endpoint; the time depends on your hardware or provider.

<div class="table-wrap"><table>
  <thead><tr><th>Round</th><th class="r">Runs</th><th class="r">Input tokens</th><th class="r">Output tokens</th><th class="r">Model time</th></tr></thead>
  <tbody>
    <tr><td>Thinking off, 4 at a time</td><td class="r">440</td><td class="r">6.8M</td><td class="r">0.3M</td><td class="r">5.7 h (about 2 h wall clock)</td></tr>
    <tr><td>Thinking on, clean (serial, cold cache)</td><td class="r">440</td><td class="r">4.8M</td><td class="r">3.4M</td><td class="r">24.6 h, plus restarts</td></tr>
  </tbody>
</table></div>

Input is dominated by L3 (the whole repository, about 32k tokens a run) and L7 (27-46k over all its turns). Output is dominated by thinking: with it on, a run writes 5-13k tokens, against about 0.5-1k without. Repo L (from the v4 round) adds about 450 configurations, and an L7 run there can read 100-250k input tokens.

## Adding a task

Each task is `tasks/<id>/task.toml` plus `hidden_tests/` and `solutions/`. The unmodified repository must fail the hidden tests and `reference.diff` must pass them and keep the repository's own tests green. Tasks that depend on repository context also keep a plausible `context_blind.diff` that fails. `tests/test_tasks_verified.py` checks all of it.
