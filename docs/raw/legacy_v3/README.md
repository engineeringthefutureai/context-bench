# Legacy round v3 (prompt v3, 21–23 November 2026)

The raw runs of the round before the published one (v4). Kept so individual runs can be linked and read;
the site's Results, Data and Cases pages show the v4 round only. Snapshot tag: `round-v3`.

- **Model:** Qwen 3.8 27B (FP8, vLLM), one shot per configuration (seed 1).
- **Repos:** S and M (repo L came with v4). **Levels:** L0–L7 as they were then; L8 and L-AG didn't exist.
- **Thinking:** off (development) and on (the round's final pass). **Prompt v3:** every level, L7 included,
  was given the target file (v4 dropped it from the shared prompt).
- **Runs:** 934 (494 thinking off, 440 thinking on). 54 of them are L7 runs from the original agent harness (no feedback on a
  failed tool call; a tool call written as text ended the run), rerun after the harness changed; `index.csv`
  marks them `superseded`. The round itself is the other 880 (the site counted 440 + 440).

## What is where

- `index.csv`: one row per run: run id, task, repo, level as the site names it, thinking, outcome, hidden
  tests, the repo's original tests broken by the patch, and the folder.
- `runs/<run_id>/`: `patch.diff` (the change as applied), `response.txt` (the model's reply), `reasoning.txt`
  (thinking, when on), `tests.txt` (hidden-test output, and the repo's own tests where they were run),
  `prompt.json` (the messages sent), `transcript.jsonl` (L7 tool calls and side exchanges), `run.json`.
- `results.jsonl`: the result rows; `grades.jsonl`: the blind judge's grades (Claude Code judge; rubric v1,
  and v2 from 22 November; never compare grades across rubric versions).

## Outcomes

Outcomes are named as on the site (PASS, FAIL, UNPARSABLE, ASKED TO EXPLORE, ...). **BROKE KNOWN TESTS** was
added on 26 November 2026 and applied to this round retroactively: the hidden tests passed, but at least one
of the repository's original tests no longer passes after the patch (it fails, its module no longer imports,
or it is gone). Those runs were PASS when the round was recorded. `known_tests_broken` in `results.jsonl`
names the tests. 6 runs of this round have that outcome, none of them superseded.

Paths on the machine that ran the round were replaced (`~/`, sandbox prefixes removed).
