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
import json
import re
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
DOCS = ROOT / "docs"

SITE_URL = "https://context.engineeringthefuture.ai"
SITE_NAME = "Context Bench"
REPO_URL = "https://github.com/engineeringthefutureai/context-bench"
AUTHOR = "Kiryl Dubarenka"
OG_IMAGE = "og-image.png"
OG_IMAGE_TYPE = "image/png"
OG_IMAGE_WIDTH = 1200
OG_IMAGE_HEIGHT = 630
OG_IMAGE_ALT = ("Context Bench experiment report: How context building affects generated code — "
                "pass rate climbing from 23% at L1 to 96% at L8 across 30 tasks and 2,122 runs on Qwen 3.8 27B.")

# (href, label, page name): the top bar, in order
NAV = [("./", "Results", "index"), ("method", "Method", "method"), ("levels", "Levels", "levels"), ("tasks", "Tasks", "tasks"), ("cases", "Cases", "cases"),
       ("guide", "Guide", "guide"), ("data", "Raw data", "data"), ("downloads", "Downloads", "downloads")]

# Footer: the round the site reports and when its results were final (fixed, so a rebuild is reproducible).
ROUND = "Prompt v4 round"
UPDATED = "26 November 2026"
UPDATED_ISO = "2026-11-26"

_HEADER = re.compile(r"\A<!--\n(.*?)\n-->\n", re.S)


def asset(name: str) -> str:
    """A docs/ file with a content version, e.g. "site.css?v=1a2b3c4d"; external URLs are left as they are."""
    if "://" in name:
        return name
    path = DOCS / name
    version = hashlib.sha256(path.read_bytes()).hexdigest()[:8] if path.is_file() else "0"
    return f"{name}?v={version}"


def _attr(text: str) -> str:
    return html.escape(text, quote=False).replace('"', "&quot;")


def page_url(page: str) -> str:
    return f"{SITE_URL}/" if page == "index" else f"{SITE_URL}/{page}"


def _seo_tags(page: str, title: str, description: str) -> str:
    """Canonical/robots, Open Graph, Twitter Card, and Schema.org JSON-LD tags for <head>."""
    if page == "404":
        return '<meta name="robots" content="noindex">\n'
    url = page_url(page)
    og_image = f"{SITE_URL}/{asset(OG_IMAGE)}"
    og_type = "website" if page == "index" else "article"
    t, d, alt = _attr(title), _attr(description), _attr(OG_IMAGE_ALT)
    ld: dict = {
        "@context": "https://schema.org",
        "@type": "TechArticle" if page == "index" else "WebPage",
        "name": title,
        "description": description,
        "url": url,
        "image": f"{SITE_URL}/{OG_IMAGE}",
        "inLanguage": "en",
        "dateModified": UPDATED_ISO,
        "author": {"@type": "Person", "name": AUTHOR},
        "isPartOf": {"@type": "WebSite", "name": SITE_NAME, "url": f"{SITE_URL}/"},
    }
    if page == "index":
        ld["headline"] = title
        ld["license"] = "https://opensource.org/licenses/MIT"
        ld["sameAs"] = REPO_URL
    ld_json = json.dumps(ld, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return (
        f'<link rel="canonical" href="{url}">\n'
        f'<meta property="og:site_name" content="{SITE_NAME}">\n'
        f'<meta property="og:type" content="{og_type}">\n'
        f'<meta property="og:title" content="{t}">\n'
        f'<meta property="og:description" content="{d}">\n'
        f'<meta property="og:url" content="{url}">\n'
        f'<meta property="og:image" content="{og_image}">\n'
        f'<meta property="og:image:type" content="{OG_IMAGE_TYPE}">\n'
        f'<meta property="og:image:width" content="{OG_IMAGE_WIDTH}">\n'
        f'<meta property="og:image:height" content="{OG_IMAGE_HEIGHT}">\n'
        f'<meta property="og:image:alt" content="{alt}">\n'
        f'<meta name="twitter:card" content="summary_large_image">\n'
        f'<meta name="twitter:title" content="{t}">\n'
        f'<meta name="twitter:description" content="{d}">\n'
        f'<meta name="twitter:image" content="{og_image}">\n'
        f'<meta name="twitter:image:alt" content="{alt}">\n'
        f'<script type="application/ld+json">{ld_json}</script>\n'
    )


def sitemap_xml() -> str:
    """XML sitemap for all indexable site pages (NAV order, excluding 404)."""
    urls = "\n".join(
        f"  <url><loc>{page_url(name)}</loc><lastmod>{UPDATED_ISO}</lastmod></url>"
        for _, _, name in NAV
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}\n"
        "</urlset>\n"
    )


def llms_txt() -> str:
    """llmstxt.org index of the site's pages, key findings, and machine-readable JSON endpoints."""
    page_lines = [
        f"- [Results]({page_url('index')}): Experiment report for the {ROUND} ({UPDATED}) — pass rates across "
        "single-file baselines, whole-repository prompts, retrieval context builders, signature maps, and minimal "
        "agentic harnesses, across repo sizes S/M/L, helper reuse, code quality penalties, thinking off vs on, and token cost."
    ]
    for _, label, name in NAV:
        if name == "index":
            continue
        meta, _ = load_page(name)
        desc = (
            "How context-bench builds the model's context across every strategy family — from single-file and "
            "whole-repository prompts to BM25/embedding retrieval, signature maps, and minimal tool-using agentic "
            "harnesses: what goes into the prompt, how retrieval ranks, and which tools the agents get."
            if name == "levels" else meta["description"]
        )
        page_lines.append(f"- [{label}]({page_url(name)}): {desc}")
    pages = "\n".join(page_lines)
    return (
        f"# {SITE_NAME}\n\n"
        "> Empirical benchmark measuring how context building affects LLM-generated code. One model "
        "(Qwen 3.8 27B FP8 on vLLM) solves the same 30 coding tasks across 3 repository sizes (S, M, L) "
        "and 2,122 runs, varying only how context is assembled — from single-file and hand-picked-file baselines "
        "and whole-repository prompts to retrieval context builders (BM25 and embeddings), signature maps, "
        "minimal agentic harnesses (file-search and symbol-editing agents), and Antigravity as an external harness reference.\n\n"
        f"- Round: {ROUND}, results as of {UPDATED}\n"
        "- Key findings (thinking on): Minimal agentic harnesses passed 92% (file-search agent) and 96% "
        "(symbol-reading, in-place-editing agent) overall (≥82% on every repo size), compared with 23% for the "
        "single-file baseline and 67% for the best retrieval context builder. Clean passes rose from 10% "
        "(single-file baseline) to 82% (symbol-editing agent). Helper reuse rose from 3% when unseen to 95% "
        "with full source in context.\n"
        f"- Author: {AUTHOR} · License: MIT\n\n"
        "## Pages\n\n"
        f"{pages}\n\n"
        "## Machine-Readable Data\n\n"
        f"- [Runs index (JSON)]({SITE_URL}/raw/index.json): Metadata, outcomes, token counts, reuse flags, and "
        "judge scores for every published run.\n"
        f"- [Tasks catalog (JSON)]({SITE_URL}/raw/tasks.json): All 30 tasks with prompts, target files, needed "
        "helpers, decoys, hidden tests, reference solutions, and difficulty ratings.\n"
        f"- [Per-run bundles]({SITE_URL}/raw/runs/): Individual run JSON files (`{SITE_URL}/raw/runs/<run_id>.json`) "
        "containing segmented prompts, reasoning, tool transcripts, patches, test output, and judge penalties.\n\n"
        "## Optional\n\n"
        f"- [GitHub Repository]({REPO_URL}): Harness code, fixture repositories, task definitions, and grading scripts.\n"
        f"- [Experiment Design]({REPO_URL}/blob/main/docs/design.md): Architecture and task design notes.\n"
        f"- [Sitemap]({SITE_URL}/sitemap.xml): XML sitemap of all indexable pages.\n"
    )


def render(content: str, *, page: str, title: str, description: str, scripts: list[str] = (),
           main_class: str = "") -> str:
    """A full HTML page: the layout around content (the inside of <main>, footer excluded)."""
    nav = "".join(f'<a href="{href}"{" aria-current=\"page\"" if name == page else ""}>{label}</a>'
                  for href, label, name in NAV)
    tags = "".join(f'<script src="{asset(s)}" defer></script>\n' for s in scripts)
    return Template((SITE / "layout.html").read_text()).substitute(
        title=html.escape(title, quote=False), nav=nav,
        description=_attr(description), seo=_seo_tags(page, title, description),
        content=content.strip("\n"), scripts=tags, style_css=asset("style.css"), site_css=asset("site.css"),
        main_attr=f' class="{main_class}"' if main_class else "",
        analytics_js=asset("analytics.js"), round=ROUND, updated=UPDATED)


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
