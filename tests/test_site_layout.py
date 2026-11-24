"""The site's one layout and its pages (scripts/site_layout.py)."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import site_layout as L  # noqa: E402


def test_every_page_renders_with_one_layout():
    for name in L.page_names():
        page = L.render_page(name)
        assert page.count('<header class="topbar">') == 1 and page.count("<footer>") == 1, name
        assert "$" not in re.sub(r"<pre.*?</pre>", "", page, flags=re.S).split("<main")[0], name   # no placeholder left
    assert 'aria-current="page">Method' in L.render_page("method")


def test_assets_carry_a_content_version():
    v = L.asset("site.css")
    assert re.fullmatch(r"site\.css\?v=[0-9a-f]{8}", v)
    assert L.asset("https://cdn.example/x.js") == "https://cdn.example/x.js"


def test_markdown_page_sections_prose_and_raw_html(tmp_path, monkeypatch):
    pages = tmp_path / "pages"
    pages.mkdir()
    (pages / "demo.md").write_text("<!--\ntitle: T\ndescription: D \"quoted\"\neyebrow: E\nheading: H\nlede: The `x` lede.\n-->\n"
                                   "Intro text.\n\n## First\n\nA paragraph with `code`.\n\n"
                                   "<div class=\"table-wrap\"><table><tr><td class=\"r\">1</td></tr></table></div>\n\n"
                                   "## Second\n\n- one\n- two\n")
    monkeypatch.setattr(L, "SITE", tmp_path)
    (tmp_path / "layout.html").write_text((ROOT / "site" / "layout.html").read_text())
    page = L.render_page("demo")
    assert "<title>T</title>" in page and 'content="D &quot;quoted&quot;"' in page
    assert '<div class="eyebrow">E</div>' in page and '<p class="lede">The <code>x</code> lede.</p>' in page
    assert page.count("<section>") == 2 and '<p class="prose">A paragraph with <code>code</code>.</p>' in page
    assert page.count("table-wrap") == 1 and '<td class="r">1</td>' in page         # raw HTML untouched


def test_built_pages_in_docs_are_up_to_date():
    """docs/<page>.html must be what site/pages renders now (asset versions included): run
    `python3 scripts/build_pages.py` after changing a page, the layout, or a CSS/JS file."""
    stale = [n for n in L.page_names() if (ROOT / "docs" / f"{n}.html").read_text() != L.render_page(n)]
    assert not stale, f"rebuild with scripts/build_pages.py: {stale}"
