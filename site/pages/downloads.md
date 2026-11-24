<!--
title: Context Bench · Downloads: Data and Versions
description: Raw results, grades and run bundles from context-bench, with versions pinned.
eyebrow: Downloads
heading: Data and versions
-->

## Rounds

Each round is pinned to a git tag. Everything on this site comes from the round listed first.

<div class="table-wrap"><table>
  <thead><tr><th>Round</th><th>Code</th><th>Model</th><th>Embedder</th><th>Grading</th></tr></thead>
  <tbody>
    <tr><td>Prompt v3: thinking off (4 at a time) and thinking on (clean), repos S and M, 440 + 440 runs</td>
        <td><a href="https://github.com/engineeringthefutureai/context-bench/tree/round-v3"><code>round-v3</code></a></td>
        <td>Qwen 3.8 27B, FP8 on vLLM (TP=2, MTP speculative decoding), 96k context, server-default temperature, seed 1</td>
        <td>qwen3-embedding:0.6b (Ollama)</td>
        <td>Comparison judge, rubric v2, agy</td></tr>
    <tr><td>Prompt v4: adds repo L, the L7 file tree and L8 (experimental edit-tools agent); in progress</td><td>main</td><td>same</td><td>EmbeddingGemma 2 (Ollama, 8k window, never truncated)</td><td>Penalty grading (planned)</td></tr>
  </tbody>
</table></div>

<div class="todo"><b>To come</b>results.jsonl and per-run bundle archives for each round.</div>

## Already here

- [raw/index.json](raw/index.json): one row per run shown in the raw-data viewer.
- [raw/tasks.json](raw/tasks.json): every task with its hidden tests and solutions.
- `raw/runs/<run_id>.json`: one run's prompt (by part), thinking, answer, transcript, patch and test output.
