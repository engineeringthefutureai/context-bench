#!/usr/bin/env python3
"""Write the hand-written pages (site/pages/*.md and *.html) into the site, through the one layout
(scripts/site_layout.py). The Results page (index.html) is built by build_report.py, the raw data and tasks
JSON by build_site.py.

  python3 scripts/build_pages.py [--out=docs]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import site_layout as L  # noqa: E402

if __name__ == "__main__":
    out = Path(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--out=")), L.DOCS))
    out = out if out.is_absolute() else L.ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    for name in L.page_names():
        (out / f"{name}.html").write_text(L.render_page(name))
    (out / "sitemap.xml").write_text(L.sitemap_xml())
    (out / "llms.txt").write_text(L.llms_txt())
    print(f"wrote {len(L.page_names())} pages + sitemap.xml + llms.txt to {out}: {', '.join(L.page_names())}")
