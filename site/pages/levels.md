<!--
title: Context Bench · Levels: How Each Variant Is Built
description: How context-bench builds the model's context at every level and variant, from L0 to the experimental L8: what goes into the prompt, how retrieval ranks, which tools the agents get and what each tool returns.
eyebrow: Levels
heading: How each level is built
lede: What the harness puts into the `<context>` block at every level and variant, how its retrievers rank code, and which tools the agents get, described from the code that runs the experiment.
-->

## What every level shares

Every run sends the same system prompt, the same task text and the same output instructions, and the levels differ only in the `<context>` block between them (L8, whose output instructions differ, is the one documented exception). Files always appear in the same form, a `File: path` line followed by the file's full content, so a file looks identical whether L1 shows it as the target, L3 as part of the whole repository or L4 as a retrieval hit. When a change has to create a file that doesn't exist yet, every level shows its path together with the line "(This file does not exist yet.)", because no search could ever find a module that isn't there. If a prompt doesn't fit the model's window, the harness records the run as NO FIT and never cuts anything off.

All levels except L7 and L8 expect the answer as complete new file contents in `<file path="...">` blocks, and the harness applies those blocks to a fresh copy of the repository before it runs any tests.

## L0: no context

The model sees only the task text. Because it can't edit a file it has never seen, L0 runs only on tier-1 control tasks, which ask for a new function from a specification, and it shows what the model does with no repository at all.

## L1: the target file

The context is the target file, the one file the change is about, shown with its path. This is the chat-paste baseline, roughly what a developer gets by pasting the file being edited into a chat window. L1, like L0 and L2, runs each task once, on the smallest repository the task exists on, since these levels show only the task's own files and the answer would be the same on a bigger repository.

**L1 +hint** is the `reuse_hint` ablation arm. It adds one line to the system prompt, "Prefer reusing existing project helpers over writing new ones.", and changes nothing else, which shows how much an explicit instruction does when the model can't see the helpers anyway.

## L2: hand-picked files

The context is the target file plus the files a careful developer would add by hand, chosen deterministically: every module the target imports directly, resolved to files in the repository (relative imports included), and the target's test file `tests/test_<stem>.py` if one exists. Nothing else is added and there is no search. L2 can only help when the module holding the needed helper is already imported by the target, which is to say when the developer already knew where the helper lives. To keep that comparison honest, half of the reuse tasks have the helper's module imported by their target and half don't.

## L3: the whole project

The context is every source file in the repository, sorted by path, each in its `File:` block. On repo M that comes to about 40k tokens and fits comfortably, while repo L, at about 169k tokens, doesn't fit the 96k window, so every L3 run on repo L is recorded as NO FIT, a result in its own right.

## L4: file-level retrieval

The context is the target file followed by the three other files that rank highest for the task text, each shown whole, in rank order. The target itself is never part of the index, so all three hits are other files. Two retrievers are compared.

**L4 bm25** ranks files with Okapi BM25 over code-aware tokens: identifiers are split at underscores and at camelCase boundaries and lowercased, so `normalize_sku` becomes "normalize" and "sku", and `HTTPError` becomes "http" and "error". Each file is indexed as its path followed by its contents.

**L4 embed** ranks files by cosine similarity between embeddings of the task text and of each file, computed by EmbeddingGemma 2 (Qwen3-Embedding 0.6B in the v3 round). The embedder never truncates: a file longer than its window is split into windows of about 4k tokens, and the file scores as its best window. The same retrievers are validated before every sweep by measuring how often they rank the planted helpers near the top (`cbench validate-retrieval`).

## L5: function-level retrieval

The context is the target file followed by the eight best-ranked functions and classes from other files. Each hit is shown with its file's path and import header (once per file), then its signature, docstring and body, grouped by file in the order the files first appear. The harness fetches the top 40 hits and skips any hit that overlaps one already taken, such as a method whose class is already in, so the eight slots aren't spent twice on the same lines. Each function and class is indexed as its path, its qualified name and its source.

The variants differ in the retriever and in what the retriever searches with:

- **L5 bm25 task_vs_code** and **L5 embed task_vs_code** search the function index with the task text, the naive baseline, using BM25 or embeddings as at L4.
- **L5 bm25 model_queries** and **L5 embed model_queries** first make a separate planning call. The model sees the target file and the task, as at L1, and is asked for up to five search queries for code relevant to the change, as a JSON list. The planning instruction never mentions reuse, helpers or existing code. The harness runs every query through the retriever, takes the top three hits of each, merges them round-robin by rank (every query's first hit, then every second hit, and so on) and keeps at most eight, the same budget as the other variants. If the reply doesn't parse, the task text becomes the only query and the run says so. The planning call's tokens count toward the run's cost.
- **L5 embed task_vs_code +hint** adds the `reuse_hint` line, as at L1, to see whether the instruction matters once relevant code is actually in view.

Two further modes are specified but not yet built, so the sweep skips them: `task_vs_summary`, which would search one-line summaries of each function instead of its code, and `draft_then_search`, in which the model would first draft the change and the draft would serve as the query.

## L6: the repository map

The context is the target file in full plus a map of the rest of the repository: one line per function and class, grouped by file, with methods indented under their class and no bodies at all. Classes also list their annotated fields, since a dataclass's fields are its constructor. The map gives cheap, global awareness of what exists, in the style of Aider's repository map, and shows whether a signature alone is enough for the model to use a helper correctly.

**L6+docs** adds the first line of each docstring to its map entry. Docstrings help when they are true and mislead when they have drifted from the code, which the trap task tests directly, and the two variants side by side show which effect wins.

## L7: the agent

At L7 the model works in a loop: it calls tools, reads their results and decides what to do next, until it answers with `<file>` blocks or runs out of budget. Since prompt v4 the prompt doesn't name the target, so the starting context is the repository's file tree, one line per file with its line count and, for Python files, how many modules import it (and how many of those are tests). Import counts are the means by which an agent can tell live code from dead code, since a file nothing imports is a strong clue, but no prompt ever says that dead code exists.

The budget is 15 tool calls. A short tool note in the context explains the mechanics: a failed call (bad arguments or an unknown tool) costs half a call and returns an error that says what was wrong, several calls can be made in one turn, and the reply with `<file>` blocks ends the task. It never says how to search or what to look for. When the budget is spent, the harness withdraws the tools and asks for the answer, and if that final reply is empty, a tool call written as text, or an answer without a usable `<file>` block, the model gets one more chance. Every tool's description states what the tool takes and returns, equally detailed for every variant, and never which tool to prefer.

**L7 grep** is the baseline agent, with three tools:

- `grep` searches file contents with a Python regular expression, in a directory, a file or a glob (every `.py` file by default), and returns up to 50 matching lines as `path:line: text`.
- `read_file` returns a whole file.
- `find_references` finds where a name is defined, imported and used, by parsing the code rather than searching text, and returns up to 50 lines grouped as definitions, imports and uses.

**L7 hybrid** adds `semantic_search`, which searches the code by meaning with the same embedder as L4 and L5 and returns the best-matching functions and classes, each with its location, signature and first docstring line. Hybrid against grep measures what embeddings add to an agent.

**L7 rich** keeps the same tool names but makes the results richer, and adds a reader for definitions:

- `grep` and `find_references` name the function, method or class that encloses each hit, with its line range, so the model can read just that part.
- `semantic_search` returns up to five hits with their source, at most 40 lines each.
- `read_file` accepts a line range and returns at most 200 lines per call.
- `read_symbol` is new: it reads up to eight functions, methods or classes by name in one call, with a method shown under its class's first line, and adds the file's imports the first time code from that file is shown.

Results are append-only, so everything the model has read stays in the conversation. Rich against hybrid measures the tools.

**L7 selective** is rich plus `drop`. Every tool result gets an ID such as `[#3]`, and the model can drop results of its latest tool turn (the "live tail"), each of which is then replaced by a one-line stub in all later turns. Only the latest results can be dropped, so everything older stays byte-identical and the server's cached prompt prefix survives up to the tail. Results are split into segments with their own IDs (one definition, one read range, a note), and a file's imports form a separate segment that the harness manages itself: it shows them once per file, stubs them automatically when the last code from that file is dropped, and shows them again with the file's next code. Dropping is free on the budget, and a drop that removes at least 300 tokens gives back a quarter of a call, at most two calls per run, which is taken back if the model reads the dropped code again. The tool note adds a factual explanation of why dropping helps, namely that every result is re-read with every later turn, and nothing about dead code, decoys or reuse. Selective against rich measures the dropping, and every run records what it dropped, how many input tokens the drops saved over later requests, and whether the needed helper was still in context when the model answered.

## L8: reading by symbol, editing in place

L8 is experimental: an attempt to push the model toward using fewer tokens by allowing it to read only selected parts of a file. At L7 (rich, selective) the model could already read by symbol, yet it kept reading whole files, so L8 removes the whole-file reader altogether. No gain in quality was expected, since the interest is in token use.

The first request gets a compact map of the code base, one line per file with its line count, how many modules import it, and the names it defines (top-level functions, classes, `Class.method` and module-level constants, in file order), without any signatures. The tools are:

- `read_symbol`, the only way to read code, takes up to eight names per call (functions, methods, classes or module-level constants, qualified or bare) or line ranges such as `shopkit/money.py:10-40`, at most 200 lines per call. Asking it for a bare path or module name returns that file's outline instead of its content.
- `grep`, `find_references` and `semantic_search` behave as in rich.
- `drop` works as in selective, refunds included.
- `edit` replaces exact text in a file of the run's copy of the repository (the old text must match exactly and occur once), and `create_file` creates a new file. The change happens at once, so later reads show the new code.

The budget is 15 points: each reading or search call costs one point, while `edit`, `create_file` and `drop` are free and keep working after the points run out, and a failed call costs half a point. After every turn the harness shows how many points are left. After each edit of a Python file, the harness compiles it, without running anything, and appends a warning with the line if the file no longer parses. A reply without tool calls ends the run, and the patch is simply the difference between the copy and the original. Two retries exist: one if the run ends without any change, and one if it ends while a changed file doesn't parse. Because the answer is a set of edits rather than `<file>` blocks, L8 uses its own output instructions, the one exception to "only the context changes", and it is reported apart from L7.

Models trained on other agent harnesses reach for tools that L8 doesn't have, such as `read_file` or `write_file`. Calls to them get a specific error that says the tool doesn't exist in this harness, points to the L8 tool that does the job (`read_symbol` for reading, `edit` or `create_file` for changes) and lists the tools that are available.

## Native tool names (thinking off only)

One extra arm ran on L7 selective and L8 with thinking off only. The tools that Qwen Code, Qwen's own coding agent, also has used its names and parameters (`read_file(file_path, offset, limit)`, `grep_search(pattern, glob, path)`, `edit(file_path, old_string, new_string)`, `write_file`), translated to the harness's tools at the boundary, with the same capabilities, results and budget. It tested whether the model does better with the tool vocabulary it was trained on. The pass rates came out about the same as with the harness's own names, so the arm was left out of the thinking-on round, and its thinking-off results are kept for reference.
