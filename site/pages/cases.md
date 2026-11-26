<!--
title: Context Bench · Case Studies: Notable Runs
description: Single runs from the context-bench v4 round that show something the aggregate numbers hide: a helper reused without being seen, an agent caught by its own test, documentation drift, an ambiguous task, and an agent harness that ran empty commands.
eyebrow: Case studies
heading: Notable runs
lede: Single runs that show something the totals on the Results page hide. Each case links to its runs in the raw-data viewer, where the prompt, the thinking, every tool call and the patch can be read in full.
-->

All six cases come from the v4 round, with Qwen 3.8 27B and one sample per configuration. They were picked because they teach something. None of them is a rate.

## Reusing a helper it never saw

Three runs called the exact helper their task needed, from the right module, although the harness had put no definition, signature or call site of it into the model's context.

| Run | What the context held | What the model wrote |
|---|---|---|
| [reuse-006 @M, L1 +hint, thinking on](data#round=all&a=5b1badf5e0129290) | Only `newsletter.py`, with nothing about validation. The reuse-hint arm told the model to reuse the project's existing helpers. | `from shopkit.utils.validation import is_valid_email` |
| [reuse-003 @M, L1, thinking off](data#round=all&a=eefc886f6461c713) | The target file already imported `from shopkit.utils.dates import utc_now`. The task asked for "five business days". | It added `add_business_days` to that same import line. |
| [reuse-001 @M, L4 bm25, thinking on](data#round=all&a=b54f73594fe674c0) | A retrieved file that imports `normalize_sku`, one of the two helpers. For the other, only its module showed, through `from shopkit.money import parse_money`. | It reused `normalize_sku` as seen and guessed `format_money` next to `parse_money`. |

Each import is a guess at a conventional name. The guesses landed because these helpers carry the names the task's own wording suggests, which is how the tier-3 tasks are built, and because a module path, a sibling function or the hint narrowed the search. Three such runs out of 118 across both rounds. The Results page keeps them in its "nothing of it in context" bucket and says so under the chart.

## The agent's own test caught its bug

[trap-001 @M, L8, thinking on](data#round=all&a=1b1a45fa90a2ce32) worked on the trap task, whose helper `format_weight` documents pounds and returns kilograms. The agent took the body into account. It fixed the helper and wrote a unit test for the shipping label, and its first fix converted the wrong way. When it tried to finish, the harness ran the repository's tests, the new one included, and handed back the failure:

<pre><code>- Weight: 3.31 lb
+ Weight: 680.39 lb</code></pre>

For 1,500 g the test expected 3.31 lb. The code printed 680.39, a multiplication where a division belonged. One more turn, and the agent corrected the conversion and passed every hidden test. That second chance comes once, only when an agent tries to finish, and no prompt mentions it beforehand. Across both rounds 24 agent runs got one, 23 for a file that no longer parsed and this single run for failing tests, and 18 of the 24 pass.

Runs like this one are why the harness checks an agent's work after it submits. The other kind is a file that doesn't parse although the model had the right code. In [cross-003 @M, L7 selective, thinking on](data#round=all&a=7d283de8b9a2952f), the thinking contains `return "\n".join(lines)`, and the answer came out as:

<pre><code>return "\n".join_placeholder" if False else "\n".join(lines)</code></pre>

Duplicated or swapped tokens like these (`status: str | None: str | None` in another run, a `}` where the copied line had `]` in a third) look like slips in sampling one token at a time, in code the model otherwise had right. Shown the error, 17 of the 23 runs with such a file went on to pass. So the harness compiles every changed file and runs the repository's own tests, never the hidden ones, once the agent tries to finish, and returns any failure for one more turn.

## Trusting the docstring

Same trap task, thinking on, repo M, two levels:

- At [L6](data#round=all&a=d094dc209392dae9) the repository map showed only the signature and the docstring that promises pounds. The model called the helper with `"lb"` as documented and got kilograms. FAIL, with the helper reused.
- At [L7 grep](data#round=all&a=87bb31cd3bab03cd) the agent pulled up the helper's body, spotted the missing pound branch, fixed it and then reused it. PASS.

With thinking on, L4, L5, L7 and L8 each had at least one configuration that reused and fixed the helper. All four can put the body in view. L6 failed on both repos, going with the documentation it had.

## Two plausible readings of one task

control-L01 asks for a status mapping in a long, old warehouse module. The warehouse sends three-letter codes (`RCV`, `PCK`, `DSP` and five more), which the module defines as `WMS_STATUS_*` constants. The task lists the statuses as words, "received → Received".

- [L7 grep, thinking on](data#round=all&a=bdca5a4447ae25ef) mapped the codes from the module. PASS.
- [L7 grep, thinking off](data#round=all&a=9d78af741560b5d4) mapped the words. FAIL.

Read alone, the prompt supports both. The code decides, so the task checks whether the model holds the prompt up against the code. Thinking left the split where it was, with the agents picking the codes in about the same share of runs either way. The Tasks page keeps this ambiguity in the task on purpose.

## Commands that ran nothing

In [control-001 @S, L-AG, thinking on](data#round=all&a=b9d4448f2fec78b2), Qwen ran inside Google's Antigravity harness and sometimes called `run_command` with `command` and `wait_ms_before_async`. Those are its own native argument names. Antigravity's schema says `CommandLine` and `WaitMsBeforeAsync`, ignores keys it doesn't know, and so ran an empty command and reported exit code 0 with no output. The thinking shows the model hunting for an environment problem. "Strange, there's no output." Then probes like `echo hello`. Then "Maybe the output capture is failing."

A scripted test reproduced it: a misnamed or missing `CommandLine` gives exactly that empty success. A raw capture from a rerun of the same task caught the misnamed keys in Qwen's reply. Over both L-AG rounds, 613 of 1,360 shell commands ran nothing. Gemini 3.5 Flash-Lite ran the same harness on 10 of the hardest task cells without one such call, and the Levels page has the full comparison.

## Announcing an edit and stopping

In [reuse-001 @L, L-AG, thinking on](data#round=all&a=5a2153d694c2682b) the agent found both helpers the task needed and spelled out how it would use them. Its reply ended with "Now let me make the edit to the export module." No tool call came with it. Antigravity's loop ends the run on any reply without a tool call, so the patch stayed empty. The whole run produced 4,909 output tokens on prompts of at most 14.7k, far from any limit, which means the model closed its own turn. The Results page counts this as STOPPED EARLY.
