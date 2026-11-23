"""A small template engine for the storefront and admin pages (no third-party dependency).

Syntax:
    {{ order.id }}                    a value; dotted lookup tries keys, then attributes
    {{ order.total_cents|money }}     filters, chained with |, optional arguments: {{ x|truncate:20 }}
    {{ note|raw }}                    no escaping (only for trusted HTML)
    {% if order.paid %}...{% elif x %}...{% else %}...{% endif %}
    {% for line in order.lines %}...{% empty %}...{% endfor %}   loop.index / loop.first / loop.last
    {% include "partials/address.html" %}
    {% block content %}...{% endblock %} with {% extends "base.html" %} on the first line
    {# comment #}

Output is HTML-escaped unless the `raw` filter is used. Conditions accept `not`, `and`, `or`, `==`,
`!=`, `<`, `>`, `<=`, `>=` and `in` between values or literals (strings, ints, True/False/None).
Templates live in `templates/` at the repo root and are compiled once per Environment.
"""
from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from shopkit.errors import ShopError

TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates"

_TOKEN = re.compile(r"({{.*?}}|{%.*?%}|{#.*?#})", re.S)
_LITERAL = re.compile(r"""^(?:"([^"]*)"|'([^']*)'|(-?\d+))$""")


class TemplateError(ShopError):
    http_status = 500


class TemplateNotFound(TemplateError):
    pass


class _Missing:
    """An undefined value: renders as "", is falsy, and any lookup on it is missing too."""

    def __bool__(self):
        return False

    def __str__(self):
        return ""

    def __iter__(self):
        return iter(())


MISSING = _Missing()


def lookup(context: dict, expr: str):
    """Resolve "a.b.c" against the context: mapping keys first, then attributes, then int indexes."""
    expr = expr.strip()
    m = _LITERAL.match(expr)
    if m:
        if m.group(3) is not None:
            return int(m.group(3))
        return m.group(1) if m.group(1) is not None else m.group(2)
    if expr in ("True", "False", "None"):
        return {"True": True, "False": False, "None": None}[expr]
    value = context
    for part in expr.split("."):
        if value is MISSING:
            return MISSING
        if isinstance(value, dict):
            value = value.get(part, MISSING)
        elif hasattr(value, part):
            value = getattr(value, part)
            if callable(value) and not isinstance(value, type):
                try:
                    value = value()
                except TypeError:
                    pass
        elif part.isdigit() and isinstance(value, (list, tuple)) and int(part) < len(value):
            value = value[int(part)]
        else:
            value = MISSING
    return value


_OPS: dict[str, Callable] = {
    "==": lambda a, b: a == b, "!=": lambda a, b: a != b, "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b, "<": lambda a, b: a < b, ">": lambda a, b: a > b,
    "in": lambda a, b: a in b if b is not MISSING else False,
}


def evaluate(context: dict, expr: str) -> bool:
    """Truth of a condition. `or` binds weakest, then `and`, then `not`, then comparisons."""
    expr = expr.strip()
    for word in (" or ", " and "):
        if word in expr:
            parts = expr.split(word)
            results = (evaluate(context, p) for p in parts)
            return any(results) if word == " or " else all(results)
    if expr.startswith("not "):
        return not evaluate(context, expr[4:])
    for op in ("==", "!=", "<=", ">=", " in ", "<", ">"):
        if op in expr:
            left, right = expr.split(op, 1)
            a, b = lookup(context, left), lookup(context, right)
            try:
                return bool(_OPS[op.strip()](a, b))
            except TypeError:
                return False
    return bool(lookup(context, expr))


# ---- nodes ---------------------------------------------------------------------------------------
@dataclass
class Text:
    text: str

    def render(self, ctx, env) -> str:
        return self.text


@dataclass
class Var:
    expr: str
    filters: list[tuple[str, str | None]]

    def render(self, ctx, env) -> str:
        value = lookup(ctx, self.expr)
        escape = True
        for name, arg in self.filters:
            if name == "raw":
                escape = False
                continue
            fn = env.filters.get(name)
            if fn is None:
                raise TemplateError(f"unknown filter {name!r}", filter=name)
            value = fn(value) if arg is None else fn(value, lookup(ctx, arg))
        text = "" if value is None or value is MISSING else str(value)
        return html.escape(text, quote=True) if escape else text


@dataclass
class If:
    branches: list[tuple[str | None, list]]          # (condition or None for else, body)

    def render(self, ctx, env) -> str:
        for cond, body in self.branches:
            if cond is None or evaluate(ctx, cond):
                return _render(body, ctx, env)
        return ""


@dataclass
class For:
    var: str
    iterable: str
    body: list
    empty: list = field(default_factory=list)

    def render(self, ctx, env) -> str:
        items = lookup(ctx, self.iterable)
        items = list(items.items()) if isinstance(items, dict) else list(items or [])
        if not items:
            return _render(self.empty, ctx, env)
        out = []
        names = [n.strip() for n in self.var.split(",")]
        for i, item in enumerate(items):
            inner = dict(ctx)
            if len(names) > 1:
                inner.update(zip(names, item))
            else:
                inner[names[0]] = item
            inner["loop"] = {"index": i + 1, "index0": i, "first": i == 0, "last": i == len(items) - 1,
                             "length": len(items)}
            out.append(_render(self.body, inner, env))
        return "".join(out)


@dataclass
class Include:
    name: str

    def render(self, ctx, env) -> str:
        return env.get(self.name).render(ctx)


@dataclass
class Block:
    name: str
    body: list

    def render(self, ctx, env) -> str:
        override = ctx.get("__blocks__", {}).get(self.name)
        return _render(override if override is not None else self.body, ctx, env)


def _render(nodes: list, ctx: dict, env) -> str:
    return "".join(n.render(ctx, env) for n in nodes)


# ---- parsing -------------------------------------------------------------------------------------
def _parse_var(inner: str) -> Var:
    parts = [p.strip() for p in inner.split("|")]
    filters = []
    for f in parts[1:]:
        name, _, arg = f.partition(":")
        filters.append((name.strip(), arg.strip() or None))
    return Var(parts[0], filters)


def parse(source: str, name: str = "<string>") -> tuple[list, str | None]:
    """Nodes of a template, and the parent template name if it starts with {% extends %}."""
    tokens = [t for t in _TOKEN.split(source) if t]
    pos = 0
    parent = None

    def block(stop: tuple[str, ...]) -> tuple[list, str]:
        nonlocal pos
        nodes = []
        while pos < len(tokens):
            tok = tokens[pos]
            pos += 1
            if tok.startswith("{#"):
                continue
            if tok.startswith("{{"):
                nodes.append(_parse_var(tok[2:-2]))
                continue
            if not tok.startswith("{%"):
                nodes.append(Text(tok))
                continue
            stmt = tok[2:-2].strip()
            word = stmt.split(None, 1)[0] if stmt else ""
            if word in stop:
                return nodes, stmt
            if word == "if":
                branches, cond = [], stmt[2:].strip()
                while True:
                    body, end = block(("elif", "else", "endif"))
                    branches.append((cond, body))
                    if end.startswith("elif"):
                        cond = end[4:].strip()
                    elif end == "else":
                        body, end = block(("endif",))
                        branches.append((None, body))
                        break
                    else:
                        break
                nodes.append(If(branches))
            elif word == "for":
                m = re.match(r"for\s+(.+?)\s+in\s+(.+)$", stmt)
                if not m:
                    raise TemplateError(f"{name}: bad for tag: {stmt}")
                body, end = block(("empty", "endfor"))
                empty = []
                if end == "empty":
                    empty, _ = block(("endfor",))
                nodes.append(For(m.group(1), m.group(2), body, empty))
            elif word == "include":
                nodes.append(Include(stmt.split(None, 1)[1].strip().strip("\"'")))
            elif word == "block":
                bname = stmt.split(None, 1)[1].strip()
                body, _ = block(("endblock",))
                nodes.append(Block(bname, body))
            else:
                raise TemplateError(f"{name}: unknown tag {word!r}", tag=word)
        if stop:
            raise TemplateError(f"{name}: missing {{% {stop[-1]} %}}")
        return nodes, ""

    first = tokens[0].strip() if tokens else ""
    if first.startswith("{%") and first[2:-2].strip().startswith("extends"):
        parent = first[2:-2].strip().split(None, 1)[1].strip().strip("\"'")
        pos = 1
    nodes, _ = block(())
    return nodes, parent


@dataclass
class Template:
    name: str
    nodes: list
    parent: str | None
    env: "Environment"

    def blocks(self) -> dict[str, list]:
        return {n.name: n.body for n in self.nodes if isinstance(n, Block)}

    def render(self, context: dict | None = None, **kwargs) -> str:
        ctx = {**self.env.globals, **(context or {}), **kwargs}
        if self.parent:
            blocks = {**self.blocks(), **ctx.get("__blocks__", {})}
            return self.env.get(self.parent).render({**ctx, "__blocks__": blocks})
        return _render(self.nodes, ctx, self.env)


class Environment:
    def __init__(self, directory: Path | None = None, filters: dict[str, Callable] | None = None):
        from shopkit.web.filters import DEFAULT_FILTERS
        self.directory = directory or TEMPLATE_DIR
        self.filters: dict[str, Callable] = {**DEFAULT_FILTERS, **(filters or {})}
        self.globals: dict = {}
        self._cache: dict[str, Template] = {}

    def get(self, name: str) -> Template:
        if name not in self._cache:
            path = (self.directory / name).resolve()
            if not path.is_relative_to(self.directory.resolve()) or not path.is_file():
                raise TemplateNotFound(f"no template {name}", template=name)
            nodes, parent = parse(path.read_text(), name)
            self._cache[name] = Template(name, nodes, parent, self)
        return self._cache[name]

    def from_string(self, source: str) -> Template:
        nodes, parent = parse(source)
        return Template("<string>", nodes, parent, self)

    def render(self, name: str, context: dict | None = None, **kwargs) -> str:
        return self.get(name).render(context, **kwargs)


_DEFAULT: Environment | None = None


def default_env() -> Environment:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Environment()
    return _DEFAULT


def render(name: str, context: dict | None = None, **kwargs) -> str:
    return default_env().render(name, context, **kwargs)
