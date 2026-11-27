<!--
title: Context Bench · Downloads: Data and Versions
description: Raw results, grades and run bundles from context-bench, with versions pinned.
eyebrow: Downloads
heading: Data and versions
-->

## Rounds

Every round is pinned to a git tag, and the site shows the round listed first.

<div class="table-wrap"><table>
  <thead><tr><th>Round</th><th>Code</th><th>Model</th><th>Embedder</th><th>Grading</th></tr></thead>
  <tbody>
    <tr><td>Prompt v4: thinking off and thinking on (2 at a time), repos S, M and L, 1,118 + 1,004 runs including the L-AG reference</td>
        <td><a href="https://github.com/engineeringthefutureai/context-bench/tree/round-v4"><code>round-v4</code></a></td>
        <td>Qwen 3.8 27B, FP8 on vLLM (TP=2, MTP speculative decoding), 98k context, server-default temperature, seed 1</td>
        <td>EmbeddingGemma 2 (Ollama, 8k window, never truncated)</td>
        <td>Penalty grading, agy</td></tr>
    <tr><td>Prompt v3: thinking off (4 at a time) and thinking on (clean), repos S and M, 440 + 440 runs</td>
        <td><a href="https://github.com/engineeringthefutureai/context-bench/tree/round-v3"><code>round-v3</code></a></td>
        <td>Qwen 3.8 27B, FP8 on vLLM (TP=2, MTP speculative decoding), 96k context, server-default temperature, seed 1</td>
        <td>qwen3-embedding:0.6b (Ollama)</td>
        <td>Comparison judge, rubric v2, agy</td></tr>
  </tbody>
</table></div>

<div class="todo"><b>To come</b>results.jsonl and per-run bundle archives will be added for each round.</div>

## Available now

- [raw/index.json](raw/index.json): one row per run in the raw-data viewer.
- [raw/tasks.json](raw/tasks.json): every task, with its hidden tests and solutions.
- `raw/runs/<run_id>.json`: one run's prompt (split by part), the model's thinking and answer, the transcript, the patch and the test output.
- [raw/legacy_v3/](https://github.com/engineeringthefutureai/context-bench/tree/main/docs/raw/legacy_v3): the v3 round's raw runs as plain files, readable one by one on GitHub: `index.csv` (one row per run), `results.jsonl`, the judge's `grades.jsonl`, and `runs/<run_id>/` with the patch, the reply, the thinking, the test output and the prompt. The site's pages show the v4 round only.
