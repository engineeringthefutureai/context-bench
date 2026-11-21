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
