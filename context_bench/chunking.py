"""Source -> Chunks (functions/classes, methods included) via the stdlib ast module."""
from __future__ import annotations

import ast
from pathlib import Path

from .types import Chunk

_SKIP_DIRS = {"__pycache__", ".git", ".venv", "venv", ".pytest_cache", ".mypy_cache"}


def iter_py_files(repo: Path) -> list[Path]:
    """Sorted .py files, skipping caches and VCS dirs."""
    return sorted(p for p in repo.rglob("*.py")
                  if not any(part in _SKIP_DIRS or part.endswith(".egg-info")
                             for part in p.relative_to(repo).parts[:-1]))


def module_name(rel_path: str) -> str:
    """'pkg/sub/mod.py' -> 'pkg.sub.mod'; '__init__.py' maps to the package."""
    parts = rel_path.removesuffix(".py").split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _signature(node: ast.AST) -> str:
    """One line: decorators, then `def name(args) -> ret` or `class Name(bases)`. No trailing colon."""
    decorators = "".join(f"@{ast.unparse(d)} " for d in node.decorator_list)
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(b) for b in node.bases] + [ast.unparse(k) for k in node.keywords]
        return f"{decorators}class {node.name}" + (f"({', '.join(bases)})" if bases else "")
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{decorators}{prefix} {node.name}({ast.unparse(node.args)}){returns}"


def _chunk(node: ast.AST, rel_path: str, qualname: str, lines: list[str]) -> Chunk:
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    return Chunk(path=rel_path, qualname=qualname,
                 kind="class" if isinstance(node, ast.ClassDef) else "function",
                 start_line=start, end_line=node.end_lineno, signature=_signature(node),
                 docstring=ast.get_docstring(node) or "",
                 source="\n".join(lines[start - 1:node.end_lineno]))


def chunks_from_source(source: str, rel_path: str) -> list[Chunk]:
    """Top-level functions and classes, and the methods of top-level classes (qualname
    module.Class.method), in source order. Nested functions are part of their parent's source.
    A file that doesn't parse yields no chunks."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    lines, module = source.splitlines(), module_name(rel_path)
    defs = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
    chunks = []
    for node in tree.body:
        if isinstance(node, defs):
            chunks.append(_chunk(node, rel_path, f"{module}.{node.name}", lines))
            if isinstance(node, ast.ClassDef):
                chunks += [_chunk(m, rel_path, f"{module}.{node.name}.{m.name}", lines)
                           for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return chunks


def extract_chunks(repo: Path) -> list[Chunk]:
    out = []
    for p in iter_py_files(repo):
        out += chunks_from_source(p.read_text(), p.relative_to(repo).as_posix())
    return out


def import_header(source: str) -> str:
    """Leading import block of a file: from the first to the last import statement that comes before
    any other code (a module docstring is skipped). Empty if the file starts with something else."""
    try:
        body = ast.parse(source).body
    except SyntaxError:
        return ""
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    imports = []
    for node in body:
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            break
        imports.append(node)
    if not imports:
        return ""
    lines = source.splitlines()
    return "\n".join(lines[imports[0].lineno - 1:imports[-1].end_lineno])
