"""L7 — agentic. Target file as initial context, plus tools the model calls to explore the repo.

Variants:
  agent_variant = "grep"    tools: grep, list_files, read_file, find_references
  agent_variant = "hybrid"  adds semantic_search (vector index next to grep)
  agent_variant = "rich"    the hybrid tools, richer, plus read_symbol: semantic_search returns up to 5
                            hits with their source (40 lines max); grep and find_references name each
                            hit's enclosing function/method/class and its lines; read_symbol pulls
                            several definitions by name in one call; read_file takes a line range and
                            its description points to read_symbol for partial reads. Append-only.
  agent_variant = "selective"  rich + `drop`: every tool result gets an ID ([#n]); the model may drop
                            results of its latest tool turn (the "live tail"), which are replaced by a
                            one-line stub in later turns. Older results are fixed, so the cached prompt
                            prefix survives up to the tail. drop is free on the budget. Its tool note
                            adds one generic working-set line (inside <context>, like the budget line).

Active vs legacy code: the agent must be able to tell live code from dead code so it doesn't spend its
budget (or its patch) on it. It gets the *means*, never the instruction: find_references shows where a
name is used, and list_files shows how many modules import each file. Nothing in the prompt or
TOOLS_NOTE says that dead code exists or should be avoided (that would be a hint, like reuse_hint).

Budget: at most 15 tool calls; then the model must return its <file> blocks.
Malformed tool calls are counted; if the run ends with tool errors and no file blocks, the label
is "tool_error".
helper_in_context for L7 = the helper's definition appeared in any tool output.
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
MAX_LIST_LINES = 400    # list_files (recursive) and the v4 starting tree
READ_CAP = 200          # rich/selective: lines per read_file call
SEARCH_BODY_LINES = 40  # rich/selective: source lines per semantic_search hit
MAX_SYMBOLS = 8         # rich/selective: names per read_symbol call (total output also capped at READ_CAP)
MAX_TURNS = 40          # backstop: drop calls are free, so turns aren't bounded by the budget alone
# selective, prompt v4: a drop that removes a sizeable result gives back part of a call, taken back again
# if the dropped code is read again; capped per run (agreed 2026-11-23)
DROP_REFUND = 0.25
DROP_REFUND_MIN_TOKENS = 300
DROP_REFUND_CAP = 2.0
_SKIP = {"__pycache__", ".git", ".pytest_cache"}

# Mechanics only (what the model has), never strategy (how to search). Goes inside <context>, after
# the target file, so the system prompt stays identical across levels.
TOOLS_NOTE = """You can explore the repository with these tools: {tools}.
You may make at most {max_calls} tool calls. When you are done, reply with the <file> blocks as
described; that reply ends the task."""

# prompt v4 (all variants): mechanics of the budget, nothing about how to search or what to look for.
TOOLS_NOTE_V4 = """The repository's files are listed above. You can explore the code with these tools: {tools}.
You have a budget of {max_calls} tool calls. A call that fails (wrong arguments, unknown tool) costs half a
call, and its error says what was wrong. You can make several calls in one turn. When you are done, reply
with the <file> blocks of your change; that reply ends the task."""

# selective, prompt v4: why and how to drop (mechanics; nothing about dead code, decoys or reuse)
SELECTIVE_NOTE_V4 = """Every tool result stays in the conversation and is re-read with every later turn, so results you
don't need make each turn longer and add noise. Each result has an ID like [#3]. With drop you can remove
results of your latest calls; each is replaced by a one-line stub, and you can read it again later. drop
doesn't count toward the budget, and a drop that removes at least {min_tokens} tokens gives back a quarter
of a call (at most {cap:g} calls per task); reading the dropped code again takes that quarter back."""

# selective only: the working-set line (generic engineering habit; says nothing about dead code,
# decoys or reuse).
SELECTIVE_NOTE = """Each tool result has an ID like [#3] and stays in the conversation until you drop it; only
the results of your latest tool calls can be dropped. After each result, keep what helps with the change
and drop what doesn't."""

# Sent when a reply holds what looks like a tool call written as text (the server couldn't parse it
# into a real one) and no <file> blocks. Generic format feedback, nothing about the task.
UNPARSED_CALL = ("error: your reply looks like a tool call, but it could not be parsed, so nothing was run. "
                 "Call tools through the tool-calling interface, or reply with the <file> blocks of your change.")
_TEXT_CALL = re.compile(r"<tool_call>|<function[=_\s>]|<invoke\b")

# Sent once the budget is spent (mechanics: the tools are gone, the answer is due).
BUDGET_SPENT = "No tool calls left. Reply with the <file> blocks of your change."

_TOOLS = {
    "grep": ("Search file contents with a Python regular expression. Returns matching lines as "
             "\"path:line: text\".",
             {"pattern": {"type": "string", "description": "regular expression"},
              "path_glob": {"type": "string", "description": "files to search, default \"**/*.py\""}},
             ["pattern"]),
    "list_files": ("List the files under a directory, recursively: each file with its line count, and for "
                   ".py files how many modules import them.",
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
# rich/selective descriptions (same tool names; grep and list_files are unchanged)
_TOOLS_RICH = {
    "grep": ("Search file contents with a Python regular expression. Returns matching lines as "
             "\"path:line in name (lines a-b): text\", where name is the enclosing function, method or class "
             "(\"module level\" outside them), so you can read just that part with read_symbol.",
             {"pattern": {"type": "string", "description": "regular expression"},
              "path_glob": {"type": "string", "description": "files to search, default \"**/*.py\""}},
             ["pattern"]),
    "read_symbol": ("Read the full source of functions, methods or classes by name, several in one call. "
                    "Names can be qualified (\"shopkit.money.format_money\", \"shopkit.orders.models.Order.total\") "
                    "or bare (\"format_money\"); a bare name that matches several definitions lists them.",
                    {"symbols": {"type": "array", "items": {"type": "string"},
                                 "description": f"up to {MAX_SYMBOLS} names"}},
                    ["symbols"]),
    "read_file": ("Read a file, or a range of its lines (1-based, inclusive). Without a range, the whole "
                  f"file. At most {READ_CAP} lines per call. To read particular functions or classes, "
                  "read_symbol is cheaper; read a whole file when you need several parts of it and don't "
                  "know their names.",
                  {"path": {"type": "string", "description": "relative to the repository root"},
                   "start": {"type": "integer", "description": "first line, default 1"},
                   "end": {"type": "integer", "description": "last line, default the end of the file"}},
                  ["path"]),
    "find_references": ("Find where a name is defined, imported and used (parsed, not text search). "
                        "Returns \"path:line in function (lines a-b): source line\" grouped as "
                        "definitions, imports, uses.",
                        {"symbol": {"type": "string", "description": "a name like \"format_money\" or "
                                    "a dotted path like \"shopkit.money.format_money\""}},
                        ["symbol"]),
    "semantic_search": ("Search the code by meaning. Returns the best matching functions and classes with "
                        f"their source (up to {SEARCH_BODY_LINES} lines each).",
                        {"query": {"type": "string"},
                         "k": {"type": "integer", "description": "number of results, 1-5, default 5"}},
                        ["query"]),
    "drop": ("Remove earlier tool results you no longer need from the conversation. Each is replaced by a "
             "one-line stub and can be read again. Only results of your latest tool calls can be dropped. "
             "Doesn't count toward the tool-call limit.",
             {"ids": {"type": "array", "items": {"type": "integer"},
                      "description": "result IDs, the n in [#n]"}},
             ["ids"]),
}
# prompt v4: each tool's behaviour, parameters and output in its description (equally detailed for every
# variant's tools). Facts only: what a tool returns and costs, never which tool to prefer.
_TOOLS_V4 = {
    "grep": ("Search file contents with a Python regular expression. Returns up to 50 matching lines as "
             "\"path:line: text\" (\"(+N more)\" if there are more).",
             {"pattern": {"type": "string", "description": "Python regular expression, e.g. \"def \\w*refund\""},
              "path_glob": {"type": "string", "description": "which files to search, e.g. \"shopkit/**/*.py\"; "
                            "default \"**/*.py\""}},
             ["pattern"]),
    "read_file": ("Read a whole file. Returns \"File: path\" and the file's content.",
                  {"path": {"type": "string", "description": "relative to the repository root, e.g. "
                            "\"shopkit/money.py\""}},
                  ["path"]),
    "find_references": ("Find where a name is defined, imported and used (parsed from the code, not text search). "
                        "Returns up to 50 lines \"path:line: source line\" grouped as definitions, imports, uses.",
                        {"symbol": {"type": "string", "description": "a name like \"format_money\" or a dotted path "
                                    "like \"shopkit.money.format_money\" (only the last part is matched)"}},
                        ["symbol"]),
    "semantic_search": ("Search the code by meaning. Returns the best matching functions and classes as "
                        "\"path:start-end  qualified.name\", each with its signature and first docstring line.",
                        {"query": {"type": "string", "description": "what you are looking for, in words"},
                         "k": {"type": "integer", "description": "number of results, default 5"}},
                        ["query"]),
}
_TOOLS_RICH_V4 = {
    "grep": ("Search file contents with a Python regular expression. Returns up to 50 matching lines as "
             "\"path:line in name (lines a-b): text\", where name is the enclosing function, method or class "
             "(\"module level\" outside them) and a-b its lines.",
             _TOOLS_V4["grep"][1], ["pattern"]),
    "read_symbol": ("Read the full source of functions, methods or classes by name, up to 8 in one call (one call "
                    "of the budget). Returns each as \"path:start-end  qualified.name\" and its source (a method with "
                    "its class's first line), plus the file's imports the first time code from that file is shown. "
                    "A bare name matching several definitions returns the list of matches.",
                    {"symbols": {"type": "array", "items": {"type": "string"},
                                 "description": "qualified names like \"shopkit.money.format_money\" or "
                                                "\"shopkit.orders.models.Order.total\", or bare names like \"format_money\""}},
                    ["symbols"]),
    "read_file": ("Read a file, or a range of its lines (1-based, inclusive), at most 200 lines per call. Returns "
                  "\"File: path (lines a-b of n)\" and those lines as they are in the file.",
                  {"path": {"type": "string", "description": "relative to the repository root"},
                   "start": {"type": "integer", "description": "first line, default 1"},
                   "end": {"type": "integer", "description": "last line, default the end of the file"}},
                  ["path"]),
    "find_references": ("Find where a name is defined, imported and used (parsed from the code, not text search). "
                        "Returns up to 50 lines \"path:line in name (lines a-b): source line\" grouped as "
                        "definitions, imports, uses; name is the enclosing function, method or class.",
                        _TOOLS_V4["find_references"][1], ["symbol"]),
    "semantic_search": ("Search the code by meaning. Returns up to 5 functions or classes, each as "
                        "\"path:start-end  qualified.name\" with its source (at most 40 lines; longer ones end with "
                        "\"... (N more lines)\").",
                        {"query": {"type": "string", "description": "what you are looking for, in words"},
                         "k": {"type": "integer", "description": "number of results, 1-5, default 5"}},
                        ["query"]),
    "drop": ("Remove results of your latest tool calls from the conversation; each is replaced by a one-line stub "
             "and can be read again. Doesn't count toward the budget; a drop that removes at least 300 tokens gives "
             "back a quarter of a call (taken back if you read the dropped code again).",
             {"ids": {"type": "array", "items": {"type": "integer"}, "description": "result IDs, the n in [#n]"}},
             ["ids"]),
}

_VARIANTS = {"grep": ["grep", "list_files", "read_file", "find_references"],
             "hybrid": ["grep", "list_files", "read_file", "find_references", "semantic_search"],
             "rich": ["grep", "list_files", "read_symbol", "read_file", "find_references", "semantic_search"],
             "selective": ["grep", "list_files", "read_symbol", "read_file", "find_references", "semantic_search",
                           "drop"]}
RICH_VARIANTS = {"rich", "selective"}
SEMANTIC_VARIANTS = {"hybrid", "rich", "selective"}     # need the embedding retriever


def _spec(name: str, agent_variant: str, prompt_version: int = 3) -> tuple:
    rich = agent_variant in RICH_VARIANTS
    if prompt_version >= 4:
        table = _TOOLS_RICH_V4 if rich else _TOOLS_V4
        if name in table:
            return table[name]
    if rich and name in _TOOLS_RICH:
        return _TOOLS_RICH[name]
    return _TOOLS[name]


def _usage(name: str, agent_variant: str, prompt_version: int = 3) -> str:
    """What a failed call should have looked like: the tool's arguments, or the tools that exist."""
    if name not in _variant_tools(agent_variant, prompt_version):
        return "Available tools: " + ", ".join(_variant_tools(agent_variant, prompt_version)) + "."
    _, props, required = _spec(name, agent_variant, prompt_version)
    args = [f"{k} ({v['type']}, {'required' if k in required else 'optional'})" for k, v in props.items()]
    return f"{name} takes: " + (", ".join(args) if args else "no arguments") + "."


def _variant_tools(agent_variant: str, prompt_version: int = 3) -> list[str]:
    """The variant's tools. From prompt v4 the starting context holds the whole file tree with the same
    facts list_files gives (line counts, importers), so list_files is not offered."""
    if agent_variant not in _VARIANTS:
        raise ValueError(f"unknown agent_variant {agent_variant!r}")
    names = _VARIANTS[agent_variant]
    return [n for n in names if n != "list_files"] if prompt_version >= 4 else names


def build_context(task: Task, ws: Workspace, agent_variant: str = "grep", prompt_version: int = 3) -> ContextBlock:
    """Initial context only, then TOOLS_NOTE listing the variant's tools; the rest arrives through tools.
    prompt_version <= 3: the target file. 4: no target (the agent finds the file from the task), the
    repository's file tree instead (list_files of the root: saves the opening turn almost every run spent
    listing directories)."""
    names = _variant_tools(agent_variant, prompt_version)
    if prompt_version >= 4:
        note = TOOLS_NOTE_V4.format(tools=", ".join(names), max_calls=MAX_TOOL_CALLS)
        if agent_variant == "selective":
            note += "\n" + SELECTIVE_NOTE_V4.format(min_tokens=DROP_REFUND_MIN_TOKENS, cap=DROP_REFUND_CAP)
        tree = RepoTools(ws.repo).list_files(".")
        return ContextBlock(text=f"Repository files:\n{tree}\n\n{note}", included=[])
    path = task.target_file
    if path in ws.files:
        target = f"File: {path}\n```{_lang(path)}\n{ws.files[path].rstrip()}\n```"
    else:
        target = f"File: {path}\n(This file does not exist yet.)"
    note = TOOLS_NOTE.format(tools=", ".join(names), max_calls=MAX_TOOL_CALLS)
    if agent_variant == "selective":
        note += "\n" + SELECTIVE_NOTE
    return ContextBlock(text=f"{target}\n\n{note}", included=[path])


def tool_schemas(agent_variant: str, prompt_version: int = 3) -> list[dict]:
    """OpenAI-style function tool definitions for the variant."""
    out = []
    for name in _variant_tools(agent_variant, prompt_version):
        desc, props, required = _spec(name, agent_variant, prompt_version)
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
    rich: bool = False                                     # rich/selective: use the *_rich methods
    imports_shown: set[str] = field(default_factory=set)  # rich: files whose imports are in the conversation
    last_segments: list[dict] = field(default_factory=list)  # rich: the last call's output in parts
                                                             # (imports / code / note), for selective IDs

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
        method = name + "_rich" if self.rich and hasattr(self, name + "_rich") else name
        self.last_segments = []
        out = getattr(self, method)(**args)
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
        """Every file under `directory`, recursively, one line each: .py files as
        "path  (N lines, imported by M modules)", other files as "path  (N lines)". M counts distinct
        modules in the repo that import it (absolute or relative imports, via ast), so unused files show
        "imported by 0". At most MAX_LIST_LINES lines, with a "(+N more)" note. (Until 2026-11-23 it
        listed one level: subdirectories and files.)"""
        d = self._resolve(directory)
        if not d.is_dir():
            return f"error: not a directory: {directory}"
        importers = self._importers()
        root = self.root.resolve()
        out = []
        for p in sorted(d.rglob("*")):
            rel = p.relative_to(root).as_posix()
            if not p.is_file() or _SKIP & set(p.relative_to(root).parts):
                continue
            try:
                lines = len(p.read_text().splitlines())
            except UnicodeDecodeError:
                out.append(rel)
                continue
            if p.suffix == ".py":
                n, tests = importers.get(_module(rel), (0, 0))
                out.append(f"{rel}  ({lines} lines, imported by {n} modules"
                           + (f", {tests} of them tests" if tests else "") + ")")
            else:
                out.append(f"{rel}  ({lines} lines)")
        if len(out) > MAX_LIST_LINES:
            out = out[:MAX_LIST_LINES] + [f"(+{len(out) - MAX_LIST_LINES} more)"]
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

    # ---- rich / selective variants: same names for the model, separate methods here so grep and
    # hybrid keep their exact behaviour. call() dispatches to these when `rich` is set.
    def _import_lines(self, rel: str) -> tuple[int, int, str] | None:
        """(first line, last line, text) of a file's top-level import statements, or None."""
        tree = self._trees().get(rel)
        nodes = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))] if tree else []
        if not nodes:
            return None
        src = (self.root / rel).read_text().splitlines()
        text = "\n".join("\n".join(src[n.lineno - 1:n.end_lineno]) for n in nodes)
        return nodes[0].lineno, max(n.end_lineno or n.lineno for n in nodes), text

    def _imports_seg(self, rel: str) -> list[dict]:
        """The file's imports as their own segment, the first time code from it is shown (and again
        after selective removed them with the file's last code)."""
        if rel in self.imports_shown:
            return []
        self.imports_shown.add(rel)
        imp = self._import_lines(rel)
        if imp is None:
            return []
        return [{"kind": "imports", "file": rel, "label": f"imports of {rel}", "text": f"Imports of {rel}:\n{imp[2]}"}]

    def forget_imports(self, rels: list[str]) -> None:
        """selective: these files' imports left the conversation."""
        self.imports_shown -= set(rels)

    def _emit(self, segs: list[dict]) -> str:
        self.last_segments = segs
        return "\n\n".join(x["text"] for x in segs)

    def read_file_rich(self, path: str, start: int | None = None, end: int | None = None) -> str:
        """A line range (1-based, inclusive) or the whole file, at most READ_CAP lines per call. The
        header gives the range read and the file's length; the lines themselves are verbatim (no
        line-number prefixes), so a helper's source still appears exactly as in the repo."""
        p = self._resolve(path)
        if not p.is_file():
            return f"error: no such file: {path}"
        rel = p.relative_to(self.root.resolve()).as_posix()
        lines = p.read_text().splitlines()
        n = len(lines)
        if n == 0:
            self.last_segments = [{"kind": "code", "file": rel, "label": f"read_file {rel}", "provides_imports": True,
                                   "text": f"File: {rel} (empty file)"}]
            return f"File: {rel} (empty file)"
        s = 1 if start is None else int(start)
        e = n if end is None else int(end)
        if s < 1 or e < s:
            raise ValueError("start and end are 1-based line numbers with start <= end")
        if s > n:
            return f"error: {rel} has only {n} lines"
        e = min(e, n)
        note = ""
        if e - s + 1 > READ_CAP:
            e = s + READ_CAP - 1
            note = f"; capped at {READ_CAP} lines, continue from line {e + 1}"
        imp = self._import_lines(rel)
        covers = bool(imp) and s <= imp[0] and e >= imp[1]   # the read itself shows the imports
        segs = [] if covers or not imp else self._imports_seg(rel)
        if covers:
            self.imports_shown.add(rel)
        segs.append({"kind": "code", "file": rel, "label": f"read_file {rel}:{s}-{e}", "provides_imports": covers,
                     "text": f"File: {rel} (lines {s}-{e} of {n}{note})\n" + "\n".join(lines[s - 1:e])})
        return self._emit(segs)

    def _enclosing(self, rel: str) -> list[tuple[int, int, str]]:
        """(start, end, name) of every function, method and class in a file ("Class.method")."""
        out: list[tuple[int, int, str]] = []
        def walk(node: ast.AST, prefix: str) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    name = f"{prefix}{child.name}"
                    out.append((child.lineno, child.end_lineno or child.lineno, name))
                    walk(child, name + ".")
        tree = self._trees().get(rel)
        if tree is not None:
            walk(tree, "")
        return out

    def _where(self, rel: str, line: int) -> str:
        """"in name (lines a-b)" for the innermost function/method/class holding `line`, else
        "module level"."""
        if not hasattr(self, "_encl_cache"):
            self._encl_cache = {}
        funcs = self._encl_cache.setdefault(rel, self._enclosing(rel))
        inside = [f for f in funcs if f[0] <= line <= f[1]]
        if not inside:
            return "module level"
        s, e, q = max(inside, key=lambda f: (f[0], -f[1]))       # innermost
        return f"in {q} (lines {s}-{e})"

    def grep_rich(self, pattern: str, path_glob: str = "**/*.py") -> str:
        """grep, with each hit's enclosing function/method/class and its lines (.py files)."""
        rx = re.compile(pattern)
        hits = []
        for rel in self._files():
            if not (fnmatch.fnmatch(rel, path_glob) or fnmatch.fnmatch(rel, path_glob.removeprefix("**/"))):
                continue
            try:
                lines = (self.root / rel).read_text().splitlines()
            except UnicodeDecodeError:
                continue
            for i, line in enumerate(lines, 1):
                if rx.search(line):
                    where = f" {self._where(rel, i)}" if rel.endswith(".py") else ""
                    hits.append(f"{rel}:{i}{where}: {line.strip()}")
        return "\n".join(_cap(hits)) or "(no matches)"

    def _chunk_map(self) -> dict[str, Any]:
        if not hasattr(self, "_chunks"):
            self._chunks = {c.qualname: c for rel in self._trees()
                            for c in chunks_from_source((self.root / rel).read_text(), rel)}
        return self._chunks

    def _symbol_seg(self, c: Any, tool: str, cap: int | None = None) -> dict:
        """"path:start-end  qualname", the class header line for a method, then the source."""
        parent = self._chunk_map().get(c.qualname.rsplit(".", 1)[0])
        head = [parent.source.splitlines()[0]] if parent is not None and parent.kind == "class" else []
        body = c.source.splitlines()
        if cap is not None and len(body) > cap:
            body = body[:cap] + [f"... ({len(body) - cap} more lines)"]
        return {"kind": "code", "file": c.path, "label": f"{tool} {c.qualname}",
                "text": f"{c.path}:{c.start_line}-{c.end_line}  {c.qualname}\n" + "\n".join(head + body)}

    def read_symbol(self, symbols: list[str]) -> str:
        """Full source of each named function/method/class (qualified, or bare when unambiguous; an
        ambiguous bare name lists its matches). At most MAX_SYMBOLS names and READ_CAP lines per call."""
        if isinstance(symbols, str):
            symbols = [symbols]
        if not isinstance(symbols, list) or not symbols or not all(isinstance(s, str) for s in symbols):
            raise ValueError("symbols must be a list of names")
        chunks = self._chunk_map()
        segs: list[dict] = []
        used = 0
        note = lambda text: segs.append({"kind": "note", "file": None, "label": "read_symbol note", "text": text})
        for i, raw in enumerate(symbols[:MAX_SYMBOLS]):
            name = raw.strip()
            c = chunks.get(name)
            if c is None:
                matches = [q for q in chunks if q.endswith("." + name)]
                if len(matches) == 1:
                    c = chunks[matches[0]]
                elif matches:
                    note(f"{name} matches several definitions; use a qualified name:\n" + "\n".join(
                        f"  {q}  ({chunks[q].path}:{chunks[q].start_line}-{chunks[q].end_line})"
                        for q in matches[:10]) + (f"\n  (+{len(matches) - 10} more)" if len(matches) > 10 else ""))
                    continue
                else:
                    note(f"(no function, method or class named {name})")
                    continue
            code = self._symbol_seg(c, "read_symbol")
            fresh = c.path not in self.imports_shown
            imports = self._imports_seg(c.path)
            n = sum(x["text"].count("\n") + 1 for x in imports + [code])
            if used + n > READ_CAP and segs:
                if fresh:
                    self.imports_shown.discard(c.path)      # not shown after all
                note(f"(output capped at {READ_CAP} lines; not read: {', '.join(symbols[i:MAX_SYMBOLS])})")
                break
            segs += imports + [code]
            used += n
        if len(symbols) > MAX_SYMBOLS:
            note(f"(at most {MAX_SYMBOLS} names per call; not read: {', '.join(symbols[MAX_SYMBOLS:])})")
        return self._emit(segs)

    def find_references_rich(self, symbol: str) -> str:
        """find_references, with each hit's enclosing function and its line range, so the right lines
        can be read: "path:line in func (lines a-b): <source line>" or "path:line module level: ..."."""
        name = symbol.strip().rsplit(".", 1)[-1]
        groups: dict[str, list[str]] = {"definitions": [], "imports": [], "uses": []}
        for rel, tree in self._trees().items():
            src = (self.root / rel).read_text().splitlines()
            def add(kind: str, line: int) -> None:
                entry = f"{rel}:{line} {self._where(rel, line)}: {src[line - 1].strip()}"
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

    def semantic_search_rich(self, query: str, k: int = 5) -> str:
        """Top-k (at most 5) functions/classes with their source: "path:start-end  qualname", the
        class header line for a method, then the source capped at SEARCH_BODY_LINES lines."""
        if self.make_retriever is None:
            raise RuntimeError("semantic_search is not available")
        if not hasattr(self, "_semantic"):
            self.semantic_search(query, 1)                  # builds the index (output discarded)
        segs: list[dict] = []
        for qualname, _ in self._semantic.search(query, max(1, min(int(k), 5))):
            c = self._chunks[qualname]
            segs += self._imports_seg(c.path) + [self._symbol_seg(c, "semantic_search hit", cap=SEARCH_BODY_LINES)]
        return self._emit(segs) or "(no results)"


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
                                       # turn's prompt starts with (messages are only ever appended),
                                       # when both offered the same tools (chat templates put tool
                                       # definitions at the top, so dropping them changes the prefix)
    budget_used: float = 0.0           # successful calls + failed calls x failed_call_cost
    # selective only (None otherwise)
    drops: int | None = None           # result IDs dropped
    drop_calls: int | None = None      # successful drop calls
    rereads_after_drop: int | None = None   # later read_file/read_symbol/semantic_search results overlapping a dropped one
    dropped: list[dict] | None = None  # {"id", "tool", "desc", "turn"} per dropped result
    dropped_tokens: int | None = None  # size of the dropped content minus its stubs (counted once)
    drop_refunds: int | None = None    # v4: drops that earned DROP_REFUND
    drop_refund_clawbacks: int | None = None   # v4: refunds taken back because the dropped code was read again
    drop_saved_tokens: int | None = None   # input tokens avoided: that difference, summed over every
                                       #   request sent after each drop (tokens_in + this = without drops)
    hit_turn_cap: bool = False         # MAX_TURNS reached (drop calls are free)
    answer_tool_text: str = ""         # tool output still in the conversation at the final turn


def cacheable_prefix(prev_in: int, prev_offer: bool | None, cur_in: int, cur_offer: bool) -> int:
    """Tokens of this turn's prompt that repeat the previous turn's prompt exactly: all of it when
    both turns offered the same tools (or none), else 0 (prev_offer None = no previous turn)."""
    return min(prev_in, cur_in) if prev_offer is not None and prev_offer == cur_offer else 0


def turn_prompts(messages: list[dict], transcript: list[dict]) -> list[tuple[list[dict], bool]]:
    """Rebuild each turn's request from a saved run: (messages sent, tools offered) per assistant
    reply. Tools were offered unless the turn followed the BUDGET_SPENT message.
    selective: a drop call's tool message carries "drop" (index and new content of every message it
    rewrote); replaying it puts the stubs in place, as the run did."""
    msgs, out = list(messages), []
    for m in transcript:
        if m.get("role") == "assistant":
            out.append((list(msgs), msgs[-1].get("content") != BUDGET_SPENT))
        for d in m.get("drop") or []:
            msgs[d["index"]] = {**msgs[d["index"]], "content": d["content"]}
        msgs.append({k: v for k, v in m.items() if k not in ("drop", "dropped_ids", "result_ids")})
    return out


def _describe(name: str, args: dict) -> str:
    """Short label for a tool result: what was asked, e.g. "shopkit/money.py:30-36"."""
    if name == "read_symbol":
        syms = args.get("symbols")
        return ", ".join(syms) if isinstance(syms, list) else str(syms or "")
    if name == "read_file":
        rng = f":{args.get('start') or 1}-{args['end']}" if args.get("end") else (
            f":{args['start']}-" if args.get("start") else "")
        return f"{args.get('path', '')}{rng}"
    key = {"grep": "pattern", "find_references": "symbol", "semantic_search": "query",
           "list_files": "directory"}.get(name)
    return json.dumps(args.get(key, "")) if name in ("grep", "semantic_search") else str(args.get(key, ""))


_RANGE_READ = re.compile(r"^File: (\S+) \(lines (\d+)-(\d+) of", re.M)
_RANGE_HIT = re.compile(r"^(\S+\.py):(\d+)-(\d+)  ", re.M)


def _ranges(out: str) -> list[tuple[str, int, int]]:
    """(path, start, end) of the code a read_file or semantic_search result shows."""
    return [(p, int(s), int(e)) for rx in (_RANGE_READ, _RANGE_HIT) for p, s, e in rx.findall(out)]


def _overlaps(a: list[tuple[str, int, int]], b: list[tuple[str, int, int]]) -> bool:
    return any(p == q and s <= f and t <= e for p, s, e in a for q, t, f in b)


def _assistant_message(chat) -> dict:
    """What goes back into the conversation: content and tool calls, no reasoning (Qwen templates
    drop earlier turns' thinking anyway)."""
    msg = {"role": "assistant", "content": chat.content or ""}
    if chat.tool_calls:
        msg["tool_calls"] = chat.tool_calls
    return msg


def run_agent(client, messages: list[dict], tools: RepoTools, *, agent_variant: str,
              temperature: float | None, seed: int, max_tokens: int,
              context_window: int, thinking: bool = True,
              failed_call_cost: float | None = None, prompt_version: int = 3) -> AgentResult:
    """Tool loop: call the model, execute tool calls, feed results back, stop on a final answer
    or when the budget is spent. Before every turn, check prompt tokens + max_tokens against the
    context window; if it no longer fits, stop and report no_fit (never let the server truncate).

    A turn without tool calls is the final answer. Once MAX_TOOL_CALLS calls have run, the next turn
    gets no tools and a BUDGET_SPENT user message. Tool calls beyond the budget within one turn are
    answered with an error and not executed. Bad arguments, unknown tools and tool exceptions are
    tool errors: the error text is returned to the model, and the loop goes on.

    failed_call_cost set (the current harness): a failed call is charged that much of the budget
    instead of a whole call, its error also lists the tool's valid arguments, and a final-looking
    reply that holds a tool call written as text (and no <file> blocks) is answered with
    UNPARSED_CALL instead of ending the run. None: the original harness, kept for older runs.

    selective: tool results are prefixed "[#n] tool what" and may be dropped by `drop` during the
    next turn only (the live tail). A drop puts a stub in that message for every later request; the
    transcript keeps the original result and logs the drop (see turn_prompts). drop calls cost
    nothing; a bad drop (unknown, older or already dropped ID) is a failed call. The cacheable prefix
    of a request after a drop ends at the first changed message. Other variants: unchanged."""
    schemas = tool_schemas(agent_variant, prompt_version)
    schema_tokens = client.count_tokens(json.dumps(schemas))
    msgs = list(messages)
    log = list(messages)                 # the transcript: msgs, but original results + drop records
    selective = agent_variant == "selective"
    feedback = failed_call_cost is not None
    cost = failed_call_cost if feedback else 1.0
    spent = 0.0                          # budget used: successful calls + failed calls x cost
    calls = errors = tokens_in = tokens_out = peak = cacheable = 0
    cached: int | None = 0
    prev_in, prev_offer = 0, None
    reasoning, start = [], time.monotonic()
    chat = None
    # selective state
    # Every tool result is one or more segments, each with an ID: code (a definition, a read range),
    # imports (a file's imports, shown once, managed by the harness), note, or a whole result.
    segs: dict[int, dict] = {}           # id -> {"msg", "kind", "file", "label", "text", "dropped",
                                         #        "ranges", "provides_imports"}
    msg_segs: dict[int, list[int]] = {}  # msgs index -> its segment IDs, in order
    live_imports: dict[str, int] = {}    # file -> the segment holding its imports in the conversation
    tail: list[int] = []                 # IDs of the latest batch (droppable this turn)
    changed_from: int | None = None      # first msgs index rewritten since the last request
    drops = drop_calls = rereads = turns = 0
    saving_now = dropped_tokens = drop_saved = 0   # tokens the current prompt is smaller by, thanks to drops
    refunds_earned = clawbacks = 0
    refund_total = 0.0                   # gross refunds granted (the cap applies to these)
    refunding = selective and prompt_version >= 4
    dropped: list[dict] = []
    capped = False

    def render(index: int) -> str:
        return "\n\n".join(f"[#{i} dropped: {segs[i]['label']}]" if segs[i]["dropped"]
                            else f"[#{i}] {segs[i]['label']}\n{segs[i]['text']}" for i in msg_segs[index])

    def add(msg: dict, extra: dict | None = None) -> None:
        msgs.append(msg)
        log.append({**msg, **extra} if extra else msg)

    def finish(content: str, no_fit: bool, finish_reason: str = "") -> AgentResult:
        sel = selective or None
        return AgentResult(content, calls, errors, tokens_in, tokens_out, peak, no_fit,
                           time.monotonic() - start, log[len(messages):], "\n\n".join(reasoning),
                           finish_reason, tokens_cached=cached, tokens_cacheable=cacheable,
                           budget_used=spent, drops=drops if sel else None,
                           drop_calls=drop_calls if sel else None,
                           rereads_after_drop=rereads if sel else None, dropped=dropped if sel else None,
                           dropped_tokens=dropped_tokens if sel else None,
                           drop_refunds=refunds_earned if refunding else None,
                           drop_refund_clawbacks=clawbacks if refunding else None,
                           drop_saved_tokens=drop_saved if sel else None, hit_turn_cap=capped,
                           answer_tool_text="\n\n".join(m["content"] for m in msgs if m.get("role") == "tool"))

    while True:
        offer = spent < MAX_TOOL_CALLS
        if selective and turns >= MAX_TURNS and offer:
            offer, capped = False, True
        if not offer and msgs[-1].get("content") != BUDGET_SPENT:
            add({"role": "user", "content": BUDGET_SPENT})
        prompt = client.count_prompt_tokens(msgs) + (schema_tokens if offer else 0)
        drop_saved += saving_now
        peak = max(peak, prompt)
        if prompt + max_tokens > context_window:
            return finish("", True)
        chat = client.chat(msgs, temperature=temperature, seed=seed, max_tokens=max_tokens,
                           tools=schemas if offer else None,
                           **({} if thinking else {"thinking": False}))
        turns += 1
        tokens_in += chat.tokens_in
        tokens_out += chat.tokens_out
        if changed_from is None:
            cacheable += cacheable_prefix(prev_in, prev_offer, chat.tokens_in, offer)
        elif prev_offer == offer:            # the shared prefix ends at the first rewritten message
            shared = client.count_prompt_tokens(msgs[:changed_from]) + (schema_tokens if offer else 0)
            cacheable += min(shared, prev_in, chat.tokens_in)
        changed_from = None
        prev_in, prev_offer = chat.tokens_in, offer
        cached = None if cached is None or chat.cached_tokens is None else cached + chat.cached_tokens
        peak = max(peak, chat.tokens_in)
        if chat.reasoning:
            reasoning.append(chat.reasoning)
        add(_assistant_message(chat))
        if (feedback and offer and not chat.tool_calls and "<file" not in chat.content
                and _TEXT_CALL.search(chat.content)):
            add({"role": "user", "content": UNPARSED_CALL})
            calls, errors, spent = calls + 1, errors + 1, spent + cost
            continue
        if not chat.tool_calls or not offer:
            break
        droppable, batch = set(tail), []
        for tc in chat.tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            if selective and name == "drop":
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                    ids = args.get("ids") if isinstance(args, dict) else None
                    if isinstance(ids, int):
                        ids = [ids]
                    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
                        raise ValueError("ids must be a list of result IDs")
                    imp = [i for i in ids if i in segs and segs[i]["kind"] == "imports"]
                    bad = [i for i in ids if i not in imp and (i not in droppable or segs[i]["dropped"])]
                    now = ", ".join(f"#{i}" for i in sorted(droppable)
                                    if not segs[i]["dropped"] and segs[i]["kind"] != "imports")
                    notes = []
                    if imp:
                        notes.append(f"{', '.join(f'#{i}' for i in imp)}: a file's imports leave with the last "
                                     "code from that file")
                    if bad:
                        notes.append(f"can't drop {', '.join(f'#{i}' for i in bad)}: only results of your latest "
                                     f"tool calls can be dropped (now: {now or 'none'})")
                    refusal = "; ".join(notes)
                    bad = imp + bad
                    ids = [i for i in ids if i not in bad]
                    if not ids:                      # nothing valid: a failed call
                        raise ValueError(refusal)
                    touched, files, auto = set(), set(), []
                    for i in dict.fromkeys(ids):
                        sg = segs[i]
                        sg["dropped"] = True
                        touched.add(sg["msg"])
                        dropped.append({"id": i, "tool": sg["label"].split(" ", 1)[0], "desc": sg["label"],
                                        "turn": turns, "auto": False})
                        if sg["file"]:
                            files.add(sg["file"])
                            if live_imports.get(sg["file"]) == i:     # a whole-file read held the imports
                                del live_imports[sg["file"]]
                                tools.forget_imports([sg["file"]])
                    for f in files:                  # imports go with the file's last code, if in the tail
                        still = any(x["kind"] == "code" and x["file"] == f and not x["dropped"] for x in segs.values())
                        j = live_imports.get(f)
                        if not still and j is not None and segs[j]["kind"] == "imports" and j in droppable:
                            segs[j]["dropped"] = True
                            touched.add(segs[j]["msg"])
                            del live_imports[f]
                            tools.forget_imports([f])
                            auto.append(j)
                            dropped.append({"id": j, "tool": "imports", "desc": segs[j]["label"],
                                            "turn": turns, "auto": True})
                    records = []
                    removed = 0
                    for idx in sorted(touched):
                        before = client.count_tokens(msgs[idx]["content"])
                        msgs[idx] = {**msgs[idx], "content": render(idx)}
                        delta = before - client.count_tokens(msgs[idx]["content"])
                        saving_now += delta
                        dropped_tokens += delta
                        removed += delta
                        records.append({"index": idx, "content": msgs[idx]["content"]})
                        changed_from = idx if changed_from is None else min(changed_from, idx)
                    drops += len(dict.fromkeys(ids))
                    drop_calls += 1
                    refund_note = ""
                    if refunding and removed >= DROP_REFUND_MIN_TOKENS and refund_total < DROP_REFUND_CAP:
                        amount = min(DROP_REFUND, DROP_REFUND_CAP - refund_total)
                        refund_total += amount
                        spent = max(spent - amount, 0.0)
                        refunds_earned += 1
                        rec = {"amount": amount, "clawed": False}
                        for i in dict.fromkeys(ids):
                            segs[i]["refund"] = rec
                        refund_note = f" ({amount:g} of a call back)"
                    text = "dropped " + ", ".join(f"#{i}" for i in dict.fromkeys(ids)) + (
                        f" (and #{', #'.join(map(str, auto))}: imports no longer needed)" if auto else "") + (
                        f"; {refusal}" if bad else "") + refund_note
                    add({"role": "tool", "tool_call_id": tc.get("id", ""), "content": text},
                        {"drop": records, "dropped_ids": list(dict.fromkeys(ids)) + auto})
                except Exception as e:
                    out = f"error: {type(e).__name__}: {e}"
                    if feedback:
                        out += f". {_usage(name, agent_variant, prompt_version)}"
                    calls, errors, spent = calls + 1, errors + 1, spent + cost
                    add({"role": "tool", "tool_call_id": tc.get("id", ""), "content": out})
                continue
            args = {}
            if spent >= MAX_TOOL_CALLS:
                out = "error: tool call budget spent; not executed"
                errors += 1
            else:
                calls += 1
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                    if name not in _variant_tools(agent_variant, prompt_version) or not isinstance(args, dict):
                        raise ValueError(f"unknown tool {name!r}")
                    out = tools.call(name, args)
                    spent += 1
                except Exception as e:          # the model sees the error and can retry
                    out = f"error: {type(e).__name__}: {e}"
                    if feedback:
                        out += f". {_usage(name, agent_variant, prompt_version)}"
                    tools.transcript.append(out)
                    errors += 1
                    spent += cost
            if not selective:
                add({"role": "tool", "tool_call_id": tc.get("id", ""), "content": out})
                continue
            parts = [] if out.startswith("error:") else list(getattr(tools, "last_segments", []) or [])
            if not parts:
                desc = _describe(name, args if isinstance(args, dict) else {})
                parts = [{"kind": "result", "file": None, "label": f"{name} {desc}", "text": out}]
            index, ids_here = len(msgs), []
            for part in parts:
                i = len(segs) + 1
                ranges = _ranges(part["text"]) if part["kind"] == "code" else []
                overlapping = [x for x in segs.values() if x["dropped"] and ranges and _overlaps(ranges, x["ranges"])]
                if overlapping:
                    rereads += 1
                    for x in overlapping:                    # v4: reading dropped code again returns its refund
                        rec = x.get("refund")
                        if rec and not rec["clawed"]:
                            rec["clawed"] = True
                            spent += rec["amount"]
                            clawbacks += 1
                segs[i] = {**part, "msg": index, "dropped": False, "ranges": ranges}
                if part["kind"] == "imports" or part.get("provides_imports"):
                    live_imports[part["file"]] = i
                ids_here.append(i)
            msg_segs[index] = ids_here
            batch += ids_here
            add({"role": "tool", "tool_call_id": tc.get("id", ""), "content": render(index)},
                {"result_ids": ids_here})
        tail = batch
    return finish(chat.content, False, chat.finish_reason)


def _lang(path: str) -> str:
    """Code-fence language for a file: python, or sql / html for repo L's migrations and templates."""
    return {".sql": "sql", ".html": "html"}.get(path[path.rfind("."):], "python")
