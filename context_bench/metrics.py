"""Metrics computed from what a run saw and what it wrote (no model calls), so `cbench rescore` can
backfill them from saved bundles.

- usage_visible: the needed helper was seen *in use* (imported or called), not only defined. Models
  reuse helpers they have only seen used (L4 finding, 2026-11-21).
- legacy_ctx_share: share of the context (characters, a proxy for tokens) that comes from dead code.
- legacy_used: the patch imports or references dead code.

Dead code = the [legacy] paths in helpers/registry.toml (harness-only; never shown to the model).
"""
from __future__ import annotations

import re

from .chunking import module_name

_FILE_HEADER = re.compile(r"^File: (\S+)")          # L1-L5 blocks, L7 read_file output
_PATH_LINE = re.compile(r"^\s*(\S+\.py)(?=[:\s(]|$)")  # L6 map headers, grep / refs / search / list lines
_FENCE = re.compile(r"^```\s*$")


def usage_visible(helpers: tuple[str, ...], text: str) -> bool | None:
    """Every needed helper's bare name appears in a line that imports or calls it and is not its own
    def/class line. Matches by bare name, like helper_in_context."""
    if not helpers:
        return None
    def used(name: str) -> bool:
        for line in text.splitlines():
            if re.search(rf"\b(?:def|class)\s+{re.escape(name)}\b", line):
                continue
            if re.search(rf"\b{re.escape(name)}\s*\(", line) or (
                    re.search(r"\bimport\b", line) and re.search(rf"\b{re.escape(name)}\b", line)):
                return True
        return False
    return all(used(q.rsplit(".", 1)[1]) for q in helpers)


def _is_dead(path: str | None, legacy: list[str]) -> bool:
    return bool(path) and any(path == p or (p.endswith("/") and path.startswith(p)) for p in legacy)


def legacy_ctx_share(text: str, legacy: list[str]) -> float:
    """Share of the context's characters attributed to dead paths. A line belongs to the path it
    names (grep, find_references, semantic_search, list_files and L6 map lines start with a path),
    else to the file block it sits in ("File: path" until its closing fence or the next header).
    Crude on purpose; 0.0 for an empty context."""
    total = dead = 0
    current: str | None = None      # path of the block we are in
    in_code = False                 # inside a ``` fenced file body
    for line in text.splitlines():
        size = len(line) + 1
        total += size
        if m := _FILE_HEADER.match(line):
            current, in_code, own = m.group(1), False, m.group(1)
        elif line.startswith("```") and current is not None:
            own = current
            if in_code and _FENCE.match(line):      # closing fence ends the file block
                current, in_code = None, False
            else:
                in_code = True
        elif not in_code and (m := _PATH_LINE.match(line)):
            own = m.group(1)
            if not line[:1].isspace():              # an unindented path line (L6 map header) opens a block
                current = own
        else:
            own = current
        if _is_dead(own, legacy):
            dead += size
    return round(dead / total, 4) if total else 0.0


def dead_modules(legacy: list[str], repo_files: list[str]) -> list[str]:
    """Dotted module names of the dead paths (a directory prefix covers its .py files)."""
    return sorted({module_name(f) for f in repo_files if f.endswith(".py") and _is_dead(f, legacy)})


def legacy_used(patch: str, modules: list[str]) -> bool:
    """An added line of the patch imports or names a dead module (absolute import or dotted
    reference). Editing a dead file alone does not count."""
    added = [l[1:] for l in patch.splitlines() if l.startswith("+") and not l.startswith("+++")]
    for line in added:
        for mod in modules:
            pkg, _, leaf = mod.rpartition(".")
            if re.search(rf"\b{re.escape(mod)}\b", line) or (
                    pkg and re.search(rf"\bfrom\s+{re.escape(pkg)}\s+import\b.*\b{re.escape(leaf)}\b", line)):
                return True
    return False


# ---- L7: what the tool output was made of ------------------------------------------------------------
# a block header: a definition hit ("path:a-b  qualname") or a file's imports ("Imports of path:")
_HIT = re.compile(r"^(?:\[#\d+\] [^\n]*\n)?(?:Imports of (\S+\.py):|(\S+\.py):\d+-\d+  \S)", re.M)


def relevant_files(files: dict[str, str], repo_files: list[str]) -> set[str]:
    """Files the patch changed, plus repo modules the changed code imports: what the change was built on."""
    import ast
    by_module = {module_name(p): p for p in repo_files if p.endswith(".py")}
    out = set(files)
    for rel, src in files.items():
        if not rel.endswith(".py"):
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for n in names:
                if n in by_module:
                    out.add(by_module[n])
    return out


def tool_output_breakdown(transcript: list[dict], relevant: set[str]) -> dict:
    """Characters of L7 tool output (a proxy for tokens) by kind and by relevance, from a saved transcript.

    Kinds: whole-file reads (read_file without a range, or a range covering the whole file),
    partial reads (ranged read_file, read_symbol), search (semantic_search), other (grep, list_files,
    find_references, drop replies, errors). Code from read_file / read_symbol / semantic_search is
    attributed to its file; `relevant` (relevant_files) splits it into code the change was built on and
    everything else. Original results count, also when selective dropped them later."""
    calls = {c["id"]: (c["function"]["name"], c["function"].get("arguments") or "{}")
             for m in transcript for c in (m.get("tool_calls") or [])}
    out = dict.fromkeys(("tool_out_chars", "read_whole_chars", "read_part_chars", "search_chars",
                         "other_tool_chars", "code_relevant_chars", "code_other_chars"), 0)
    for m in transcript:
        if m.get("role") != "tool":
            continue
        text = m.get("content") or ""
        out["tool_out_chars"] += len(text)
        name, raw = calls.get(m.get("tool_call_id"), ("?", "{}"))
        if "error:" in text[:200] or name not in ("read_file", "read_symbol", "semantic_search"):
            out["other_tool_chars"] += len(text)
            continue
        if name == "read_file":
            whole = re.search(r"^File: \S+(?: \(lines 1-(\d+) of (\d+)\)| \(empty file\))?$", text, re.M)
            is_whole = bool(whole) and (whole.group(1) is None or whole.group(1) == whole.group(2))
            out["read_whole_chars" if is_whole else "read_part_chars"] += len(text)
            path = (re.search(r"^File: (\S+)", text, re.M) or [None, None])[1]
            blocks = [(path, text)]
        else:
            out["read_part_chars" if name == "read_symbol" else "search_chars"] += len(text)
            starts = [(mm.start(), mm.group(1) or mm.group(2)) for mm in _HIT.finditer(text)]
            blocks = [(p, text[s:(starts[i + 1][0] if i + 1 < len(starts) else len(text))])
                      for i, (s, p) in enumerate(starts)]
            if not starts:
                blocks = [(None, text)]
        for path, chunk in blocks:
            out["code_relevant_chars" if path in relevant else "code_other_chars"] += len(chunk)
    return out
