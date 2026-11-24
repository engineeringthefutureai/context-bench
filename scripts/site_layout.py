"""The site's one layout (site/layout.html: head, top bar, footer) and the pages that go into it.

Two kinds of content:
- site/pages/<name>.md or .html: hand-written page content. It starts with a header of `key: value` lines
  (an HTML comment): title, description, optional scripts and main_class; a .md page also gives eyebrow,
  heading and lede for its page header. A .md body is Markdown: "## Heading" starts a <section>, paragraphs
  get the prose style, raw HTML blocks (tables with alignment classes, <pre>, <dl>) pass through as they
  are. A .html body is the inside of <main> as it is: the raw-data and tasks pages are such shells, their
  content loads on demand from JSON (docs/raw/, docs/tasks.json).
- generated content (the Results page): scripts/build_report.py builds the HTML and renders it here.

Assets get a version from their content (style.css?v=<hash>), so a changed file is never served stale.
`python3 scripts/build_pages.py` writes the hand-written pages to docs/; the live preview renders them on
request.
"""
from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
DOCS = ROOT / "docs"

# (href, label, page name): the top bar, in order
NAV = [("./", "Results", "index"), ("method", "Method", "method"), ("tasks", "Tasks", "tasks"),
       ("guide", "Guide", "guide"), ("data", "Raw data", "data"), ("downloads", "Downloads", "downloads")]

_HEADER = re.compile(r"\A<!--\n(.*?)\n-->\n", re.S)


def asset(name: str) -> str:
    """A docs/ file with a content version, e.g. "site.css?v=1a2b3c4d"; external URLs are left as they are."""
    if "://" in name:
        return name
    path = DOCS / name
    version = hashlib.sha256(path.read_bytes()).hexdigest()[:8] if path.is_file() else "0"
    return f"{name}?v={version}"


def render(content: str, *, page: str, title: str, description: str, scripts: list[str] = (),
           main_class: str = "") -> str:
    """A full HTML page: the layout around content (the inside of <main>, footer excluded)."""
    nav = "".join(f'<a href="{href}"{" aria-current=\"page\"" if name == page else ""}>{label}</a>'
                  for href, label, name in NAV)
    tags = "".join(f'<script src="{asset(s)}" defer></script>\n' for s in scripts)
    return Template((SITE / "layout.html").read_text()).substitute(
        title=html.escape(title, quote=False), nav=nav,
        description=html.escape(description, quote=False).replace('"', "&quot;"),
        content=content.strip("\n"), scripts=tags, style_css=asset("style.css"), site_css=asset("site.css"),
        main_attr=f' class="{main_class}"' if main_class else "",
        analytics_js=asset("analytics.js"))


def _page_file(name: str) -> Path:
    for ext in (".md", ".html"):
        if (path := SITE / "pages" / f"{name}{ext}").is_file():
            return path
    raise FileNotFoundError(f"site/pages/{name}.md or .html")


def load_page(name: str) -> tuple[dict, str]:
    """(header fields, the HTML inside <main>) of a page. scripts is a list (comma-separated)."""
    path = _page_file(name)
    text = path.read_text()
    m = _HEADER.match(text)
    if not m:
        raise ValueError(f"{path.relative_to(ROOT)} has no header comment")
    meta = dict(line.split(": ", 1) for line in m.group(1).splitlines() if line.strip())
    meta["scripts"] = [s.strip() for s in meta.get("scripts", "").split(",") if s.strip()]
    body = text[m.end():]
    return meta, (markdown_page(meta, body) if path.suffix == ".md" else body)


def page_header(eyebrow: str, heading: str, lede: str = "") -> str:
    """The block every page opens with (lede is HTML)."""
    return ('  <header style="display:grid;gap:12px">\n'
            f'    <div class="eyebrow">{html.escape(eyebrow)}</div>\n    <h1>{html.escape(heading)}</h1>\n'
            + (f'    <p class="lede">{lede}</p>\n' if lede else "") + "  </header>\n")


def markdown_page(meta: dict, body: str) -> str:
    """A .md page: its header block, then the body with one <section> per "## " heading."""
    import markdown                       # optional dependency (pip install -e '.[site]')
    out = markdown.markdown(body, extensions=["tables", "md_in_html"], output_format="html")
    out = re.sub(r"<p>", '<p class="prose">', out)
    out = re.sub(r'(?<!<div class="table-wrap">)<table>', '<div class="table-wrap"><table>', out)
    out = re.sub(r"</table>(?!</div>)", "</table></div>", out)
    lede = markdown.markdown(meta.get("lede", "")).removeprefix("<p>").removesuffix("</p>")
    parts = re.split(r"(?=<h2>)", out)
    sections = "".join(f"\n  <section>\n{s.strip()}\n  </section>\n" for s in parts[1:])
    return page_header(meta.get("eyebrow", ""), meta.get("heading", ""), lede) + parts[0].strip() + sections


def page_names() -> list[str]:
    return sorted({p.stem for p in (SITE / "pages").glob("*.md")} | {p.stem for p in (SITE / "pages").glob("*.html")})


def render_page(name: str) -> str:
    meta, content = load_page(name)
    return render(content, page=name, title=meta["title"], description=meta["description"],
                  scripts=meta["scripts"], main_class=meta.get("main_class", ""))
