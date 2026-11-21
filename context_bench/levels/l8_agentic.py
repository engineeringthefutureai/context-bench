"""L8 — agentic. Target file as initial context, plus tools the model calls to explore the repo.

Variants:
  agent_variant = "grep"    tools: grep, list_files, read_file, find_references
  agent_variant = "hybrid"  adds semantic_search (vector index next to grep)

Active vs legacy code: the agent must be able to tell live code from dead code so it doesn't spend its
budget (or its patch) on it. It gets the *means*, never the instruction: find_references shows where a
name is used, and list_files shows how many modules import each file. Nothing in the prompt or
TOOLS_NOTE says that dead code exists or should be avoided (that would be a hint, like reuse_hint).

Budget: at most 15 tool calls; then the model must return its <file> blocks.
Malformed tool calls are counted; if the run ends with tool errors and no file blocks, the label
is "tool_error".
helper_in_context for L8 = the helper's definition appeared in any tool output.
"""
from __future__ import annotations

import ast
import fnmatch
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..chunking import chunks_from_source
from ..retrieval.base import chunk_text
from ..types import ContextBlock, Task
from ..workspace import Workspace

MAX_TOOL_CALLS = 15
MAX_LINES = 50          # grep / find_references output cap, with a "(+N more)" note
_SKIP = {"__pycache__", ".git", ".pytest_cache"}

# Mechanics only (what the model has), never strategy (how to search). Goes inside <context>, after
# the target file, so the system prompt stays identical across levels.
TOOLS_NOTE = """You can explore the repository with these tools: {tools}.
You may make at most {max_calls} tool calls. When you are done, reply with the <file> blocks as
described; that reply ends the task."""

# Sent once the budget is spent (mechanics: the tools are gone, the answer is due).
BUDGET_SPENT = "No tool calls left. Reply with the <file> blocks of your change."

_TOOLS = {
    "grep": ("Search file contents with a Python regular expression. Returns matching lines as "
             "\"path:line: text\".",
             {"pattern": {"type": "string", "description": "regular expression"},
              "path_glob": {"type": "string", "description": "files to search, default \"**/*.py\""}},
             ["pattern"]),
    "list_files": ("List a directory: subdirectories, and .py files with their line count and how many "
                   "modules import them.",
                   {"directory": {"type": "string", "description": "relative to the repository root, "
                                  "default \".\""}}, []),
    "read_file": ("Read a whole file.",
                  {"path": {"type": "string", "description": "relative to the repository root"}},
                  ["path"]),
    "find_references": ("Find where a name is defined, imported and used (parsed, not text search). "
                        "Returns \"path:line: source line\" grouped as definitions, imports, uses.",
                        {"symbol": {"type": "string", "description": "a name like \"format_money\" or "
                                    "a dotted path like \"shopkit.money.format_money\""}},
                        ["symbol"]),
    "semantic_search": ("Search the code by meaning. Returns the best matching functions and classes.",
                        {"query": {"type": "string"}, "k": {"type": "integer", "description": "default 5"}},
                        ["query"]),
}
_VARIANTS = {"grep": ["grep", "list_files", "read_file", "find_references"],
             "hybrid": ["grep", "list_files", "read_file", "find_references", "semantic_search"]}


def _variant_tools(agent_variant: str) -> list[str]:
    if agent_variant not in _VARIANTS:
        raise ValueError(f"unknown agent_variant {agent_variant!r}")
    return _VARIANTS[agent_variant]


def build_context(task: Task, ws: Workspace, agent_variant: str = "grep") -> ContextBlock:
    """Initial context only: the target file, then TOOLS_NOTE listing the variant's tools.
    The rest arrives through tools."""
    names = _variant_tools(agent_variant)
    path = task.target_file
    if path in ws.files:
        target = f"File: {path}\n```python\n{ws.files[path].rstrip()}\n```"
    else:
        target = f"File: {path}\n(This file does not exist yet.)"
    note = TOOLS_NOTE.format(tools=", ".join(names), max_calls=MAX_TOOL_CALLS)
    return ContextBlock(text=f"{target}\n\n{note}", included=[path])


def tool_schemas(agent_variant: str) -> list[dict]:
    """OpenAI-style function tool definitions for the variant."""
    out = []
    for name in _variant_tools(agent_variant):
        desc, props, required = _TOOLS[name]
        out.append({"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required}}})
    return out


def _module(rel: str) -> str:
    parts = rel.removesuffix(".py").split("/")
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _is_test(rel: str) -> bool:
    return rel.startswith("tests/") or "/tests/" in rel or Path(rel).name.startswith("test_")


def _cap(lines: list[str]) -> list[str]:
    return lines[:MAX_LINES] + ([f"(+{len(lines) - MAX_LINES} more)"] if len(lines) > MAX_LINES else [])


@dataclass
class RepoTools:
    """Read-only tools over the sandbox copy of the repo."""
    root: Path
    make_retriever: Callable[[], Any] | None = None       # hybrid: builds the semantic_search index
    transcript: list[str] = field(default_factory=list)   # every tool output

    def _files(self) -> list[str]:
        return sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")
                      if p.is_file() and not _SKIP & set(p.relative_to(self.root).parts))

    def _resolve(self, rel: str) -> Path:
        p = (self.root / rel.strip().lstrip("/")).resolve()
        if p != self.root.resolve() and self.root.resolve() not in p.parents:
            raise ValueError(f"path outside the repository: {rel}")
        return p

    def _trees(self) -> dict[str, ast.AST]:
        if not hasattr(self, "_tree_cache"):
            trees = {}
            for rel in self._files():
                if rel.endswith(".py"):
                    try:
                        trees[rel] = ast.parse((self.root / rel).read_text())
                    except SyntaxError:
                        pass
            self._tree_cache = trees
        return self._tree_cache

    def call(self, name: str, args: dict) -> str:
        """Dispatch one tool call; the output (or error) is recorded in the transcript."""
        out = getattr(self, name)(**args)
        self.transcript.append(out)
        return out

    def grep(self, pattern: str, path_glob: str = "**/*.py") -> str:
        rx = re.compile(pattern)
        hits = []
        for rel in self._files():
            if not (fnmatch.fnmatch(rel, path_glob) or fnmatch.fnmatch(rel, path_glob.removeprefix("**/"))):
                continue
            try:
                lines = (self.root / rel).read_text().splitlines()
            except UnicodeDecodeError:
                continue
            hits += [f"{rel}:{i}: {line.strip()}" for i, line in enumerate(lines, 1) if rx.search(line)]
        return "\n".join(_cap(hits)) or "(no matches)"

    def _importers(self) -> dict[str, tuple[int, int]]:
        """module -> (importing modules, of which tests), counting absolute and relative imports;
        `from pkg import mod` counts for pkg.mod when that is a module."""
        trees = self._trees()
        modules = {_module(rel) for rel in trees}
        seen: dict[str, set[str]] = {}
        for rel, tree in trees.items():
            me, pkg = _module(rel), _module(rel).split(".")
            if not rel.endswith("__init__.py"):
                pkg = pkg[:-1]
            for node in ast.walk(tree):
                targets = []
                if isinstance(node, ast.Import):
                    targets = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = node.module or ""
                    if node.level:
                        up = pkg[:len(pkg) - (node.level - 1)] if node.level > 1 else pkg
                        base = ".".join(up + ([base] if base else []))
                    targets = [base] + [f"{base}.{a.name}" for a in node.names]
                for t in targets:
                    while t and t not in modules and "." in t:      # a.b.c (attribute) -> a.b
                        t = t.rsplit(".", 1)[0]
                    if t in modules and t != me:
                        seen.setdefault(t, set()).add(rel)
        return {m: (len(r), sum(map(_is_test, r))) for m, r in seen.items()}

    def list_files(self, directory: str = ".") -> str:
        """One line per entry under `directory`: subdirectories, and .py files as
        "path  (N lines, imported by M modules)". M counts distinct modules in the repo that import it
        (absolute or relative imports, via ast), so unused files show "imported by 0"."""
        d = self._resolve(directory)
        if not d.is_dir():
            return f"error: not a directory: {directory}"
        importers = self._importers()
        out = []
        for p in sorted(d.iterdir()):
            rel = p.relative_to(self.root.resolve()).as_posix()
            if p.name in _SKIP:
                continue
            if p.is_dir():
                out.append(f"{rel}/")
            elif p.suffix == ".py":
                n, tests = importers.get(_module(rel), (0, 0))
                lines = len(p.read_text().splitlines())
                out.append(f"{rel}  ({lines} lines, imported by {n} modules"
                           + (f", {tests} of them tests" if tests else "") + ")")
            else:
                out.append(rel)
        return "\n".join(out) or "(empty)"

    def read_file(self, path: str) -> str:
        p = self._resolve(path)
        if not p.is_file():
            return f"error: no such file: {path}"
        return f"File: {p.relative_to(self.root.resolve()).as_posix()}\n{p.read_text()}"

    def find_references(self, symbol: str) -> str:
        """Every place a name is defined, imported or used, as "path:line: <source line>", via ast
        (Name/Attribute/import nodes, not text search). `symbol` is a bare name ("format_money") or a
        dotted path ("shopkit.money.format_money"). Grouped as definitions, imports, uses; capped at
        50 lines with a "(+N more)" note."""
        name = symbol.strip().rsplit(".", 1)[-1]
        groups: dict[str, list[str]] = {"definitions": [], "imports": [], "uses": []}
        for rel, tree in self._trees().items():
            src = (self.root / rel).read_text().splitlines()
            def add(kind: str, line: int) -> None:
                entry = f"{rel}:{line}: {src[line - 1].strip()}"
                if entry not in groups[kind]:
                    groups[kind].append(entry)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == name:
                    add("definitions", node.lineno)
                elif isinstance(node, (ast.Import, ast.ImportFrom)) and any(
                        a.name.rsplit(".", 1)[-1] == name or a.asname == name for a in node.names):
                    add("imports", node.lineno)
                elif isinstance(node, ast.Name) and node.id == name and not isinstance(node.ctx, ast.Store):
                    add("uses", node.lineno)
                elif isinstance(node, ast.Name) and node.id == name:
                    add("definitions", node.lineno)
                elif isinstance(node, ast.Attribute) and node.attr == name:
                    add("uses", node.lineno)
        lines = [f"{k} ({len(v)}):\n" + "\n".join(f"  {e}" for e in v) for k, v in groups.items() if v]
        if not lines:
            return f"(no references to {name})"
        return "\n".join(_cap("\n".join(lines).splitlines()))

    def semantic_search(self, query: str, k: int = 5) -> str:
        """Top-k functions/classes by the configured retriever over every .py file's chunks (indexed
        as chunk_text, like L5), as "path:start-end  qualname", the signature and the first
        docstring line."""
        if self.make_retriever is None:
            raise RuntimeError("semantic_search is not available")
        if not hasattr(self, "_semantic"):
            chunks = [c for rel in self._trees()
                      for c in chunks_from_source((self.root / rel).read_text(), rel)]
            self._chunks = {c.qualname: c for c in chunks}
            self._semantic = self.make_retriever()
            self._semantic.index(list(self._chunks), [chunk_text(c) for c in self._chunks.values()])
        out = []
        for qualname, _ in self._semantic.search(query, max(1, min(int(k), 20))):
            c = self._chunks[qualname]
            doc = c.docstring.strip().splitlines()[0] if c.docstring.strip() else ""
            out.append(f"{c.path}:{c.start_line}-{c.end_line}  {qualname}\n    {c.signature}"
                       + (f"\n    {doc}" if doc else ""))
        return "\n".join(out) or "(no results)"


@dataclass
class AgentResult:
    content: str
    tool_calls: int
    tool_errors: int
    tokens_in: int
    tokens_out: int
    peak_ctx_tokens: int      # largest prompt over all turns (context grows with tool output)
    no_fit: bool              # a turn would have exceeded the context window
    latency_s: float
    transcript: list[dict] = field(default_factory=list)   # every message after the first two
    reasoning: str = ""       # all turns' reasoning, joined
    finish_reason: str = ""   # of the last turn
    tokens_cached: int | None = None   # server-reported cache hits, summed (None if any turn didn't say)
    tokens_cacheable: int = 0          # per turn after the first: the previous turn's prompt, which this
                                       # turn's prompt starts with (messages are only ever appended)


def _assistant_message(chat) -> dict:
    """What goes back into the conversation: content and tool calls, no reasoning (Qwen templates
    drop earlier turns' thinking anyway)."""
    msg = {"role": "assistant", "content": chat.content or ""}
    if chat.tool_calls:
        msg["tool_calls"] = chat.tool_calls
    return msg


def run_agent(client, messages: list[dict], tools: RepoTools, *, agent_variant: str,
              temperature: float | None, seed: int, max_tokens: int,
              context_window: int, thinking: bool = True) -> AgentResult:
    """Tool loop: call the model, execute tool calls, feed results back, stop on a final answer
    or when the budget is spent. Before every turn, check prompt tokens + max_tokens against the
    context window; if it no longer fits, stop and report no_fit (never let the server truncate).

    A turn without tool calls is the final answer. Once MAX_TOOL_CALLS calls have run, the next turn
    gets no tools and a BUDGET_SPENT user message. Tool calls beyond the budget within one turn are
    answered with an error and not executed. Bad arguments, unknown tools and tool exceptions are
    tool errors: the error text is returned to the model, and the loop goes on."""
    schemas = tool_schemas(agent_variant)
    schema_tokens = client.count_tokens(json.dumps(schemas))
    msgs = list(messages)
    calls = errors = tokens_in = tokens_out = peak = cacheable = 0
    cached: int | None = 0
    prev_in = 0
    reasoning, start = [], time.monotonic()
    chat = None
    while True:
        offer = calls < MAX_TOOL_CALLS
        if not offer and msgs[-1].get("content") != BUDGET_SPENT:
            msgs.append({"role": "user", "content": BUDGET_SPENT})
        prompt = client.count_prompt_tokens(msgs) + (schema_tokens if offer else 0)
        peak = max(peak, prompt)
        if prompt + max_tokens > context_window:
            return AgentResult("", calls, errors, tokens_in, tokens_out, peak, True,
                               time.monotonic() - start, msgs[len(messages):], "\n\n".join(reasoning),
                               tokens_cached=cached, tokens_cacheable=cacheable)
        chat = client.chat(msgs, temperature=temperature, seed=seed, max_tokens=max_tokens,
                           tools=schemas if offer else None,
                           **({} if thinking else {"thinking": False}))
        tokens_in += chat.tokens_in
        tokens_out += chat.tokens_out
        cacheable += min(prev_in, chat.tokens_in)
        prev_in = chat.tokens_in
        cached = None if cached is None or chat.cached_tokens is None else cached + chat.cached_tokens
        peak = max(peak, chat.tokens_in)
        if chat.reasoning:
            reasoning.append(chat.reasoning)
        msgs.append(_assistant_message(chat))
        if not chat.tool_calls or not offer:
            break
        for tc in chat.tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            if calls >= MAX_TOOL_CALLS:
                out = "error: tool call budget spent; not executed"
                errors += 1
            else:
                calls += 1
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                    if name not in _variant_tools(agent_variant) or not isinstance(args, dict):
                        raise ValueError(f"unknown tool {name!r}")
                    out = tools.call(name, args)
                except Exception as e:          # the model sees the error and can retry
                    out = f"error: {type(e).__name__}: {e}"
                    tools.transcript.append(out)
                    errors += 1
            msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": out})
    return AgentResult(chat.content, calls, errors, tokens_in, tokens_out, peak, False,
                       time.monotonic() - start, msgs[len(messages):], "\n\n".join(reasoning),
                       chat.finish_reason, tokens_cached=cached, tokens_cacheable=cacheable)
