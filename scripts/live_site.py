#!/usr/bin/env python3
"""Live preview of the site: the pages and the raw-data viewer read the current state from disk on every
request, instead of a periodic rebuild into a folder.

  python3 scripts/live_site.py [--port=8766] [--results=results]

- Static files come from docs/ as they are (pages, CSS, JS).
- /raw/index.json and /raw/tasks.json are built from results/ (build_site.select_rows and friends) and
  rebuilt only when results.jsonl, grades or penalties change.
- /raw/runs/<run_id>.json is built when asked for and cached until that run's files change.
- / (index.html) is the Results or in-progress page (build_report.build), built on each request; while a
  sweep runs (results/sweep_status.json) it refreshes itself every 30 s.
The published site (docs/, scripts/build_site.py) is unchanged: this is for watching a round in progress.
Binds 0.0.0.0. No leak scan: it serves the local machine's results to the local network.
"""
from __future__ import annotations

import functools
import http.server
import json
import sys
import threading
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--port=")), 8766))
sys.path.insert(0, str(ROOT / "scripts"))
_argv, sys.argv = sys.argv, [sys.argv[0]] + [a for a in sys.argv[1:] if a.startswith("--results=")]
import build_report as br  # noqa: E402  (both read --results at import)
import build_site as bs    # noqa: E402
sys.argv = _argv

DOCS = ROOT / "docs"
_lock = threading.Lock()
_state: dict = {"key": None, "rows": {}, "index": b"", "tasks": b""}
_runs: dict[str, tuple[float, bytes]] = {}


def _mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _key() -> tuple:
    return tuple(_mtime(bs.RESULTS / f) for f in ("results.jsonl", "grades.jsonl", "penalties.jsonl"))


def _run_mtime(run_id: str) -> float:
    d = bs.RESULTS / "runs" / run_id
    return max((_mtime(f) for f in d.iterdir()), default=0.0) if d.is_dir() else 0.0


def _payload(r: dict) -> bytes:
    m = _run_mtime(r["run_id"])
    hit = _runs.get(r["run_id"])
    if hit and hit[0] == m:
        return hit[1]
    data = json.dumps(bs.run_payload(r), separators=(",", ":")).encode()
    _runs[r["run_id"]] = (m, data)
    return data


def refresh() -> None:
    """Re-read the results when one of their files changed (under _lock)."""
    key = _key()
    if key == _state["key"]:
        return
    bs.GRADES.clear(); bs.GRADES.update(bs.load_grades())
    bs.PENALTIES.clear(); bs.PENALTIES.update(bs.load_penalties())
    for td in sorted((ROOT / "tasks").iterdir()):
        if (td / "task.toml").is_file():
            bs.TARGETS[td.name] = tomllib.loads((td / "task.toml").read_text()).get("target_file", "")
    rows = bs.select_rows()
    _state["rows"] = {r["run_id"]: r for r in rows}
    index = [json.loads(_payload(r))["meta"] for r in rows]
    index.sort(key=lambda m: (m["task_id"], m["level_label"], m["repo_size"], m["round"]))
    _state["index"] = json.dumps(index, separators=(",", ":")).encode()
    _state["tasks"] = json.dumps(bs.tasks_payload(), separators=(",", ":")).encode()
    _state["key"] = key


def home() -> bytes:
    page = br.build()
    if br.sweep_status():                    # a sweep is running: refresh on its own
        page = page.replace("<head>", '<head>\n<meta http-equiv="refresh" content="30">', 1)
    return page.encode()


class Handler(http.server.SimpleHTTPRequestHandler):
    def _send(self, body: bytes, ctype: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path in ("/", "/index", "/index.html"):
                with _lock:
                    refresh()
                return self._send(home(), "text/html; charset=utf-8")
            if path in ("/raw/index.json", "/raw/tasks.json"):
                with _lock:
                    refresh()
                    body = _state["index"] if path.endswith("index.json") else _state["tasks"]
                return self._send(body, "application/json")
            if path.startswith("/raw/runs/") and path.endswith(".json"):
                run_id = path.rsplit("/", 1)[1][:-5]
                with _lock:
                    refresh()
                    r = _state["rows"].get(run_id)
                    body = _payload(r) if r else None
                if body is None:
                    return self.send_error(404, "no such run")
                return self._send(body, "application/json")
        except Exception as e:               # keep serving; show what broke
            return self.send_error(500, f"{type(e).__name__}: {e}")
        if not (DOCS / path.lstrip("/")).exists() and (DOCS / (path.lstrip("/") + ".html")).exists():
            self.path = path + ".html"        # /data -> data.html, as GitHub Pages does
        return super().do_GET()

    def end_headers(self):
        if not self.path.startswith("/raw/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    with _lock:
        refresh()
    print(f"live site on 0.0.0.0:{PORT}: {len(_state['rows'])} runs from {bs.RESULTS}", flush=True)
    server = http.server.ThreadingHTTPServer(("0.0.0.0", PORT),
                                             functools.partial(Handler, directory=str(DOCS)))
    server.serve_forever()
