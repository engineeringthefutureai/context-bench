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
