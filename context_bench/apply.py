"""Model output -> files in a sandbox copy of the repo."""
from __future__ import annotations

import difflib
import re
import shutil
from pathlib import Path

_FILE_RE = re.compile(r'<file\s+path\s*=\s*["\']([^"\']+)["\']\s*>\n?(.*?)</file>', re.DOTALL | re.IGNORECASE)
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_FENCE_RE = re.compile(r"\A\s*```[\w.+-]*\n(.*?)\n?```\s*\Z", re.DOTALL)


class UnsafePath(ValueError):
    """A <file> path that escapes the sandbox."""


def parse_files(text: str) -> dict[str, str]:
    """Extract <file path="..."> blocks; tolerate code fences inside a block.

    Stray <think>...</think> sections are dropped first. If a path appears twice, the last block wins.
    Contents always end with a single newline."""
    files = {}
    for path, body in _FILE_RE.findall(_THINK_RE.sub("", text)):
        if m := _FENCE_RE.match(body):
            body = m.group(1)
        files[path.strip()] = body.rstrip("\n") + "\n"
    return files


_OPEN_RE = re.compile(r'<file\s+path\s*=\s*["\']([^"\']+)["\']\s*>\n?', re.IGNORECASE)
_CODE_FENCE_RE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)\n?```", re.DOTALL | re.IGNORECASE)
_TOOL_RE = re.compile(r"<(function_calls|invoke|tool_call|read_file|search_files|list_files|"
                      r"search_code|grep|open_file)\b", re.IGNORECASE)
_CODE_HINT_RE = re.compile(r"^\s*(def |class |import |from \S+ import )", re.MULTILINE)


def parse_files_lenient(text: str, target_file: str) -> dict[str, str]:
    """For learning only, never for the label: what the model most likely meant when the strict
    parse found nothing. In order:
      1. strict <file> blocks;
      2. <file path=...> openings whose closing tag is missing: content runs to the next opening,
         or to the end (a wrapping code fence is removed);
      3. otherwise the last ```python fenced block, taken as the task's target file.
    Returns {} when there is no code at all."""
    text = _THINK_RE.sub("", text)
    if files := parse_files(text):
        return files
    openings = list(_OPEN_RE.finditer(text))
    files = {}
    for i, m in enumerate(openings):
        end = openings[i + 1].start() if i + 1 < len(openings) else len(text)
        body = re.sub(r"</file>\s*$", "", text[m.end():end].strip(), flags=re.IGNORECASE)
        body = re.sub(r"\n?```\s*$", "", body.strip())
        body = re.sub(r"^```[\w+-]*[ \t]*\n", "", body)      # and its opening line
        if body.strip():
            files[m.group(1).strip()] = body.rstrip("\n") + "\n"
    if files:
        return files
    blocks = [b for b in _CODE_FENCE_RE.findall(text) if b.strip()]
    return {target_file: blocks[-1].rstrip("\n") + "\n"} if blocks else {}


def format_status(text: str, strict_files: dict[str, str]) -> str:
    """How the reply relates to the output protocol:
    "ok" (strict <file> blocks), "tool_request" (pretend tool calls: the model asked to explore),
    "bad_format" (code present, tags missing or broken), "no_code" (prose only)."""
    if strict_files:
        return "ok"
    text = _THINK_RE.sub("", text)
    if _TOOL_RE.search(text):
        return "tool_request"
    if "```" in text or _OPEN_RE.search(text) or _CODE_HINT_RE.search(text):
        return "bad_format"
    return "no_code"


def make_sandbox(repo: Path, dst: Path) -> Path:
    """Fresh copy of the repo for one run (dst is replaced if it exists)."""
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(repo, dst, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    return dst


def apply_files(sandbox: Path, files: dict[str, str]) -> dict[str, str | None]:
    """Write files; return {rel_path: previous contents or None}. Raise UnsafePath on escape.

    Every path is checked before anything is written, so an unsafe path leaves the sandbox untouched."""
    root = sandbox.resolve()
    targets = {}
    for rel in files:
        target = (root / rel).resolve()
        if Path(rel).is_absolute() or not target.is_relative_to(root) or target == root:
            raise UnsafePath(rel)
        targets[rel] = target
    before = {}
    for rel, target in targets.items():
        before[rel] = target.read_text() if target.is_file() else None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(files[rel])
    return before


def unified_patch(before: dict[str, str | None], files: dict[str, str]) -> str:
    """Unified diff of the change, saved per run for auditing. Unchanged files are left out."""
    parts = []
    for rel in sorted(files):
        old, new = before.get(rel), files[rel]
        if old == new:
            continue
        parts.extend(difflib.unified_diff(
            (old or "").splitlines(keepends=True), new.splitlines(keepends=True),
            fromfile="/dev/null" if old is None else f"a/{rel}", tofile=f"b/{rel}"))
    return "".join(parts)


# ---- L8 edit tool (prompt arm "edit_tools") ---------------------------------------------------------

def _find(content: str, old: str) -> list[tuple[int, int]]:
    """Spans of `old` in `content`: exact matches, else matches line by line ignoring trailing whitespace."""
    if old.strip() == "":
        return []
    spans, i = [], content.find(old)
    while i != -1:
        spans.append((i, i + len(old)))
        i = content.find(old, i + 1)
    if spans:
        return spans
    lines, want = content.splitlines(keepends=True), [l.rstrip() for l in old.splitlines()]
    offs = [0]
    for l in lines:
        offs.append(offs[-1] + len(l))
    for s in range(len(lines) - len(want) + 1):
        if all(lines[s + k].rstrip() == want[k] for k in range(len(want))):
            end = offs[s + len(want)]
            if content[offs[s]:end].endswith("\n") and not old.endswith("\n"):
                end -= 1
            spans.append((offs[s], end))
    return spans


def changed_files(original: Path, sandbox: Path) -> tuple[dict[str, str | None], dict[str, str]]:
    """L8: what the agent's edits changed, as (before, files) like apply_files/unified_patch use: every text
    file in the sandbox that is new or differs from the original repo."""
    before, files = {}, {}
    for p in sorted(sandbox.rglob("*")):
        if not p.is_file() or {"__pycache__", ".pytest_cache"} & set(p.parts):
            continue
        rel = p.relative_to(sandbox).as_posix()
        try:
            now = p.read_text()
        except UnicodeDecodeError:
            continue
        orig = original / rel
        then = orig.read_text() if orig.is_file() else None
        if now != then:
            before[rel], files[rel] = then, now
    return before, files
