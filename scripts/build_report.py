"""Write docs/index.html (the Results page) from results/results.jsonl and results/grades.jsonl.

    python scripts/build_report.py

Numbers come from the data; the narrative around them is written for the prompt-v3 round (final round =
thinking on, clean mode; compared with the thinking-off development round at the same prompt version).
Rerun after the data changes; reread the narrative after a new round."""
from __future__ import annotations

import collections
import html
import json
import os
import re
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# which round's data: results/ (the current round) or an archive, e.g. results/archive-v3
RESULTS = Path(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--results=")), ROOT / "results"))
if not RESULTS.is_absolute():
    RESULTS = ROOT / RESULTS
sys.path.insert(0, str(ROOT / "scripts"))
from build_site import level_label, outcome  # noqa: E402

# Illustrative API prices (USD per million tokens), stated on the page. Not quotes.
PRICES = {"open-weights host": (0.20, 0.80), "frontier API": (3.00, 15.00)}
LEVEL_ORDER = ["L0", "L1", "L1 +hint", "L2", "L3", "L4 bm25", "L4 embed", "L5 bm25 task_vs_code",
               "L5 bm25 model_queries", "L5 embed task_vs_code", "L5 embed task_vs_code +hint", "L5 embed model_queries",
               "L6", "L6+docs", "L7 grep", "L7 hybrid", "L7 rich", "L7 selective"]


def e(x) -> str:
    return html.escape(str(x))


def load():
    rows = [json.loads(line) for line in open(RESULTS / "results.jsonl")]
    grades = {}
    for line in (open(RESULTS / "grades.jsonl") if (RESULTS / "grades.jsonl").exists() else []):
        g = json.loads(line)
        if str(g.get("rubric_version")) == "2":
            grades[g["run_id"]] = g
    key = lambda r: (r["task_id"], r["level"], r["retriever"], r["query_mode"], r["agent_variant"], r["prompt_arm"],
                     r["repo_size"], bool(r.get("map_docs")))
    pv = max((r.get("prompt_version", 1) for r in rows), default=3)          # the round's prompt version
    final = {key(r): r for r in rows if r.get("thinking") and r.get("cold_start") and r.get("prompt_version") == pv}
    off = {}
    for r in rows:
        if not r.get("thinking") and r.get("prompt_version") == pv and not (r["level"] == "L7" and r.get("failed_call_cost") is None):
            off[key(r)] = r
    return final, off, grades


def classify(r) -> str:
    if r["label"] == "ok":
        return "pass"
    if r.get("format_status") != "ok" or r["label"] == "tool_error":
        return "invalid"
    try:
        t = (RESULTS / "runs" / r["run_id"] / "tests.txt").read_text()
    except FileNotFoundError:
        t = ""
    if "SyntaxError" in t or "IndentationError" in t or ("error during collection" in t and r["tests_passed"] == 0):
        return "broken"
    if r.get("repo_tests_total") and r["repo_tests_passed"] < r["repo_tests_total"]:
        return "flagged"
    return "silent"


def pct(a, b) -> str:
    return f"{100 * a / b:.0f}%" if b else "–"


def link(run_id: str, text: str) -> str:
    return f'<a href="data#round=all&a={e(run_id)}">{e(text)}</a>'


def obar(label: str, counts: dict, n: int, cls: str = "", scale: int = 0) -> str:
    """One configuration's outcomes on a common scale: slots for tasks this level doesn't run (L0 runs only
    the tier-1 tasks, L1-L2 each task once on its smallest repo) are drawn as "not run", so a bar's
    length is comparable across levels."""
    not_run = max(scale - n, 0)
    names = {"pass": "PASS", "fail": "FAIL", "invalid": "INVALID", "oot": "OUT OF TOKENS", "nofit": "NO FIT",
             "running": "in progress", "queued": "queued"}
    parts = "".join(f'<span class="o-{k}" style="flex:{counts.get(k, 0)}" title="{names[k]}: {counts.get(k, 0)}"></span>'
                    for k in ("pass", "fail", "invalid", "oot", "nofit", "running", "queued") if counts.get(k))
    if not_run:
        parts += f'<span class="o-na" style="flex:{not_run}" title="not run at this level: {not_run}"></span>'
    note = f' <span class="small">({not_run} n/a)</span>' if not_run else ""
    return (f'<div class="obar {cls}"><span class="lab">{e(label)}</span><span class="track" role="img" '
            f'aria-label="{e(label)}: {counts.get("pass", 0)} of {n} pass, {not_run} not run">{parts}</span>'
            f'<span class="v">{counts.get("pass", 0)}/{n}{note}</span></div>')


def sweep_status() -> dict | None:
    """results/sweep_status.json (written by cbench run), only while its sweep process is alive."""
    path = RESULTS / "sweep_status.json"
    try:
        st_ = json.loads(path.read_text())
        os.kill(int(st_["pid"]), 0)
        return st_
    except (OSError, ValueError, KeyError):
        return None


def build_progress(final: dict, off: dict) -> str:
    """While a round is in progress (no final-round runs yet): per configuration, a bar of the outcomes
    recorded so far plus what the running sweep has in progress and queued; then outcomes per repo."""
    status = sweep_status()
    pending = collections.defaultdict(collections.Counter)      # (round, label) -> running / queued
    for r in (status or {}).get("runs", {}).values():
        rnd = "final" if r.get("thinking") else "off"
        pending[rnd, level_label(r)][r["state"]] += 1

    def bars(rows, rnd, title):
        by = collections.defaultdict(list)
        for r in rows:
            by[level_label(r)].append(r)
        labels = set(by) | {lab for (x, lab) in pending if x == rnd}
        if not labels:
            return f"<h3>{e(title)}</h3><p class=small>Nothing recorded or queued yet.</p>"
        order = [l for l in LEVEL_ORDER if l in labels] + sorted(labels - set(LEVEL_ORDER))
        totals = {lab: len(by[lab]) + sum(pending[rnd, lab].values()) for lab in order}
        scale = max(totals.values())
        out = []
        for i, lab in enumerate(order):
            oc = collections.Counter(outcome(r) for r in by[lab])
            pend = pending[rnd, lab]
            counts = {"pass": oc["PASS"], "fail": oc["FAIL"], "invalid": oc["INVALID"], "oot": oc["OUT OF TOKENS"],
                      "nofit": oc["NO FIT"], "running": pend["running"], "queued": pend["queued"]}
            group = lab.split()[0].replace("+docs", "")
            prev = order[i - 1].split()[0].replace("+docs", "") if i else group
            bar = obar(lab, counts, totals[lab], "group-start" if group != prev else "", scale=scale)
            extra = " · ".join(x for x in (f"{pend['running']} running" if pend["running"] else "",
                                            f"{pend['queued']} queued" if pend["queued"] else "") if x)
            if extra:
                bar = bar.replace('</span></div>', f' <span class="small">{extra}</span></span></div>', 1)
            out.append(bar)
        return f"<h3>{e(title)}</h3><div class=obars>{''.join(out)}</div>"

    def table(rows, title):
        by = collections.defaultdict(collections.Counter)
        for r in rows:
            by[r["repo_size"]][outcome(r)] += 1
        cols = ["PASS", "FAIL", "INVALID", "OUT OF TOKENS", "NO FIT"]
        body = "".join(f"<tr><td>{s}</td>" + "".join(f"<td class=r>{c[k]}</td>" for k in cols)
                       + f"<td class=r>{sum(c.values())}</td></tr>" for s, c in sorted(by.items()))
        return (f"<h3>{e(title)}</h3><div class=table-wrap><table><thead><tr><th>Repo</th>"
                + "".join(f"<th class=r>{k}</th>" for k in cols) + "<th class=r>Runs</th></tr></thead>"
                f"<tbody>{body or '<tr><td colspan=7>none yet</td></tr>'}</tbody></table></div>")

    legend = ('<div class="olegend small">' + "".join(
        f'<span><i class="o-{k}"></i>{n}</span>' for k, n in (("pass", "PASS"), ("fail", "FAIL"), ("invalid", "INVALID"),
                                                          ("oot", "OUT OF TOKENS"), ("nofit", "NO FIT"),
                                                          ("running", "in progress"), ("queued", "queued"))) + "</div>")
    if status:
        runs = status.get("runs", {}).values()
        live = (f'<p class="small">Sweep running since {e(status.get("started_at", "")[:16].replace("T", " "))} UTC: '
                f'{sum(r["state"] == "running" for r in runs)} in progress, {sum(r["state"] == "queued" for r in runs)} '
                f'queued (last update {e(status.get("updated_at", "")[11:16])} UTC).</p>')
    else:
        live = '<p class="small">No sweep running right now.</p>'
    return HEAD + f"""
<main>
  <header style="display:grid;gap:12px">
    <div class="eyebrow">Round in progress · preview</div>
    <h1>How context building affects generated code</h1>
    <p class="lede">The current round is still running. This page shows what has been recorded so far; the
    full write-up comes when the clean thinking-on round is done. Browse the runs in the raw-data viewer.</p>
  </header>
  <section>
    <h2>Per configuration</h2>
    {live}
    {legend}
    {bars(list(off.values()), "off", "Thinking off")}
    {bars(list(final.values()), "final", "Final round (thinking on, cold cache)")}
    <p class="small">Each bar is one configuration, on a common scale; its length is the runs recorded plus the
    ones the running sweep still has. The number is PASS out of that total. Outcomes as defined on the Method page.</p>
  </section>
  <section>
    <h2>Per repo</h2>
    {table(list(off.values()), "Thinking off")}
    {table(list(final.values()), "Final round (thinking on, cold cache)")}
  </section>
""" + FOOT


def build() -> str:
    final, off, grades = load()
    if not final:
        return build_progress(final, off)
    F = list(final.values())
    common = [k for k in final if k in off]
    by = collections.defaultdict(list)
    for r in F:
        by[level_label(r)].append(r)
    order = [l for l in LEVEL_ORDER if l in by] + sorted(set(by) - set(LEVEL_ORDER))
    oc_all = collections.Counter(outcome(r) for r in F)
    n_all = len(F)

    # ---- per level rows
    rows_html, bars = [], []
    scale = max(len(by[name]) for name in order)
    for i, name in enumerate(order):
        xs = by[name]
        oc = collections.Counter(outcome(r) for r in xs)
        counts = {"pass": oc["PASS"], "fail": oc["FAIL"], "invalid": oc["INVALID"], "oot": oc["OUT OF TOKENS"]}
        group = name.split()[0].replace("+docs", "")
        prev = order[i - 1].split()[0].replace("+docs", "") if i else group
        bars.append(obar(name, counts, len(xs), "group-start" if group != prev else "", scale=scale))
        reuse = [r["reuse_passed"] for r in xs if r.get("reuse_passed") is not None]
        ok = [r for r in xs if r["label"] == "ok"]
        kept = sum(1 for r in ok if r.get("repo_tests_total") and r["repo_tests_passed"] == r["repo_tests_total"])
        rk = [grades[r["run_id"]]["rank_pct"] for r in xs if r["run_id"] in grades and grades[r["run_id"]].get("rank_pct") is not None]
        rows_html.append(
            f"<tr><td>{e(name)}</td><td class=r>{len(xs)}</td><td class=r>{oc['PASS']}</td><td class=r>"
            f"{(str(sum(reuse)) + '/' + str(len(reuse))) if reuse else '–'}</td><td class=r>{kept}/{len(ok)}</td>"
            f"<td class=r>{(f'{st.mean(rk):.2f}') if rk else '–'}</td>"
            f"<td class=r>{st.median(r['tokens_in'] for r in xs) / 1000:.1f}k</td>"
            f"<td class=r>{st.median(r['tokens_out'] for r in xs) / 1000:.1f}k</td>"
            f"<td class=r>{st.median(r['latency_s'] for r in xs):.0f}</td></tr>")

    # ---- reuse by visibility
    vis = collections.defaultdict(lambda: [0, 0])
    vis_off = collections.defaultdict(lambda: [0, 0])
    for src, tgt in ((F, vis), ([off[k] for k in common], vis_off)):
        for r in src:
            if r.get("reuse_passed") is None:
                continue
            k = ("full source" if r.get("helper_body_in_context") else "a call site" if r.get("helper_usage_in_context")
                 else "signature only" if r.get("helper_in_context") else "not seen")
            tgt[k][0] += 1
            tgt[k][1] += bool(r["reuse_passed"])
    vis_rows = "".join(f"<tr><td>{k}</td><td class=r>{vis[k][1]}/{vis[k][0]} ({pct(vis[k][1], vis[k][0])})</td>"
                       f"<td class=r>{vis_off[k][1]}/{vis_off[k][0]} ({pct(vis_off[k][1], vis_off[k][0])})</td></tr>"
                       for k in ("full source", "a call site", "signature only", "not seen"))

    # ---- thinking on vs off
    t_on = collections.Counter(classify(final[k]) for k in common)
    t_off = collections.Counter(classify(off[k]) for k in common)
    nc = len(common)
    think_cls = [("pass", "PASS"), ("invalid", "No usable answer (any harness notices)"),
                 ("broken", "Code doesn't import (any test run catches it)"),
                 ("flagged", "Wrong; the repo's own tests fail (a test loop would flag it)"),
                 ("silent", "Wrong; the repo's own tests pass (silent)")]
    think_rows = "".join(f"<tr><td>{lab}</td><td class=r>{t_off[k]} ({pct(t_off[k], nc)})</td>"
                         f"<td class=r>{t_on[k]} ({pct(t_on[k], nc)})</td></tr>" for k, lab in think_cls)
    lv_think = collections.defaultdict(lambda: [0, 0, 0])
    for k in common:
        a = lv_think[k[1]]
        a[0] += 1
        a[1] += off[k]["label"] == "ok"
        a[2] += final[k]["label"] == "ok"
    think_lv_rows = "".join(f"<tr><td>{l}</td><td class=r>{a[1]}/{a[0]}</td><td class=r>{a[2]}/{a[0]}</td>"
                            f"<td class=r>{a[2] - a[1]:+d}</td></tr>" for l, a in sorted(lv_think.items()))
    out_off = sum(off[k]["tokens_out"] for k in common) / nc
    out_on = sum(final[k]["tokens_out"] for k in common) / nc

    # ---- latency buckets
    buckets = ["<1 min", "1-3 min", "3-10 min", ">10 min"]
    lat = collections.defaultdict(collections.Counter)
    for r in F:
        s = r["latency_s"]
        lat[r["level"]]["<1 min" if s < 60 else "1-3 min" if s < 180 else "3-10 min" if s < 600 else ">10 min"] += 1
    lat_rows = []
    for l in sorted(lat):
        xs = [r for r in F if r["level"] == l]
        okx = [r for r in xs if r["label"] == "ok"]
        lat_rows.append(f"<tr><td>{l}</td>" + "".join(f"<td class=r>{lat[l][b]}</td>" for b in buckets)
                        + f"<td class=r>{st.median(r['latency_s'] for r in xs):.0f}</td>"
                        + f"<td class=r>{(sum(r['latency_s'] for r in xs) / len(okx) / 60):.1f}</td></tr>" if okx else "")

    # ---- cost
    cost_rows = []
    for l in sorted({r["level"] for r in F}):
        xs = [r for r in F if r["level"] == l]
        okn = sum(r["label"] == "ok" for r in xs)
        tin = sum(r["tokens_in"] for r in xs)
        cached = sum((r.get("tokens_cached") or 0) for r in xs)
        tout = sum(r["tokens_out"] for r in xs)
        cells = []
        for pin, pout in PRICES.values():
            usd = (tin * pin + tout * pout) / 1e6
            cells.append(f"<td class=r>${usd / okn * 100:.2f}</td>" if okn else "<td class=r>–</td>")
        cost_rows.append(f"<tr><td>{l}</td><td class=r>{tin / len(xs) / 1000:.1f}k</td>"
                         f"<td class=r>{cached / len(xs) / 1000:.1f}k</td><td class=r>{tout / len(xs) / 1000:.1f}k</td>"
                         f"<td class=r>{(tin + tout) / okn / 1000:.1f}k</td>" + "".join(cells) + "</tr>")
    price_note = "; ".join(f"{name}: ${p[0]:.2f} input / ${p[1]:.2f} output per million tokens" for name, p in PRICES.items())

    # ---- agents
    agents = []
    for v in ("grep", "hybrid", "rich", "selective"):
        xs = [r for r in F if r["level"] == "L7" and r["agent_variant"] == v]
        if not xs:
            continue
        reuse = [r["reuse_passed"] for r in xs if r.get("reuse_passed") is not None]
        agents.append(f"<tr><td>{v}</td><td class=r>{sum(r['label'] == 'ok' for r in xs)}/{len(xs)}</td>"
                      f"<td class=r>{sum(reuse)}/{len(reuse)}</td>"
                      f"<td class=r>{st.median(r['tokens_in'] for r in xs) / 1000:.1f}k</td>"
                      f"<td class=r>{st.median((r.get('tokens_cached') or 0) for r in xs) / 1000:.1f}k</td>"
                      f"<td class=r>{st.median(r['tool_calls'] for r in xs):.0f}</td>"
                      f"<td class=r>{st.median(r['latency_s'] for r in xs):.0f}</td></tr>")
    sel = [r for r in F if r["level"] == "L7" and r["agent_variant"] == "selective"]
    sel_drop_runs = sum(1 for r in sel if r.get("drops"))
    sel_dropped = sum(r.get("dropped_tokens") or 0 for r in sel)
    sel_saved = sum(r.get("drop_saved_tokens") or 0 for r in sel)
    sel_in = sum(r["tokens_in"] for r in sel)
    calls = collections.Counter()
    for r in [r for r in F if r["level"] == "L7" and r["agent_variant"] in ("rich", "selective")]:
        p = RESULTS / "runs" / r["run_id"] / "transcript.jsonl"
        if p.exists():
            for line in p.open():
                m = json.loads(line)
                for c in m.get("tool_calls") or []:
                    calls[c["function"]["name"]] += 1

    # ---- judge
    agree = collections.Counter()
    for r in F:
        g = grades.get(r["run_id"])
        if g and g.get("correctness") is not None:
            agree[(r["label"] == "ok", g["correctness"] >= 4)] += 1
    graded = sum(agree.values())
    passed_cov = [r for r in F if r["label"] == "ok" and r.get("patch_coverage") is not None]
    low_cov = [r for r in passed_cov if r["patch_coverage"] < 0.8]
    tests_added = sum(1 for r in F if (r.get("tests_added") or 0) > 0)

    # ---- gallery
    def find(task, lvl, size="M"):
        return next((r["run_id"] for r in F if r["task_id"] == task and level_label(r) == lvl and r["repo_size"] == size), "")
    broke = [r for r in F if r["label"] == "ok" and r.get("repo_tests_total") and r["repo_tests_passed"] < r["repo_tests_total"]]
    oot = next((r for r in F if outcome(r) == "OUT OF TOKENS"), None)
    dead = [r for r in F if r.get("legacy_used")]

    l7g = by.get("L7 grep", [])
    l3 = by.get("L3", [])
    fresh_ratio = (st.median(r["tokens_in"] - (r.get("tokens_cached") or 0) for r in l7g)
                   / st.median(r["tokens_in"] for r in l3)) if l7g and l3 else 0
    p = lambda xs: sum(r["label"] == "ok" for r in xs)

    page = HEAD + f"""
<main>
  <header style="display:grid;gap:12px">
    <div class="eyebrow">Experiment report · prompt v3 round</div>
    <h1>How context building affects generated code</h1>
    <p class="lede">The same coding tasks, the same model and eight ways of building its context, from no repository
    at all to an agent that explores with tools. {n_all} final-round runs: thinking on, a cold prefix cache before
    every run, one sample per configuration.</p>
    <div class="meta"><span>Qwen 3.8 27B FP8 on vLLM</span><span>20 tasks on repos S and M</span><span>compared with a thinking-off round of the same configurations</span></div>
  </header>

  <section class="takeaways breakout" aria-label="Key findings">
    <div class="takeaway"><b>Thinking beats any context level</b><span>{pct(t_off['pass'], nc)} → {pct(t_on['pass'], nc)} PASS over the same {nc} configurations. Answers with no usable code: {t_off['invalid']} → {t_on['invalid']}.</span></div>
    <div class="takeaway"><b>The agent leads, the whole repo is close</b><span>L7 grep {p(l7g)}/{len(l7g)}, L3 {p(l3)}/{len(l3)}, on {fresh_ratio:.0%} of L3's fresh input per run.</span></div>
    <div class="takeaway"><b>Seeing the helper drives reuse</b><span>Full source in context: {vis['full source'][1]}/{vis['full source'][0]} reused. Not seen: {vis['not seen'][1]}/{vis['not seen'][0]}.</span></div>
    <div class="takeaway"><b>About one answer in six is silently wrong</b><span>{pct(t_on['silent'], nc)} with thinking, {pct(t_off['silent'], nc)} without: wrong, yet the repo's own tests pass. Thinking doesn't change that.</span></div>
  </section>

  <section>
    <div class="eyebrow">Results</div>
    <h2>Pass rate by context level</h2>
    <p class="prose">Every bar is one configuration over its tasks (27 runs = the 20 tasks, 7 of them on both repos; L0 and L1-L2 run
    each task once, on its smallest repo). <span class="num-good">PASS</span>: a valid answer and every hidden test passes. <span class="num-bad">FAIL</span>:
    a valid answer, a hidden test fails. Grey: no accepted answer, or the output limit was hit. Hatched (n/a):
    tasks the level doesn't run, so every bar is on the same scale.</p>
    <div class="obars">{''.join(bars)}</div>
    <div class="table-wrap"><table>
      <thead><tr><th>Configuration</th><th class=r>Runs</th><th class=r>PASS</th><th class=r>Reuse</th><th class=r>Repo tests kept</th>
      <th class=r>Judge rank</th><th class=r>Input</th><th class=r>Output</th><th class=r>Seconds</th></tr></thead>
      <tbody>{''.join(rows_html)}</tbody></table></div>
    <p class="small">Reuse: reuse tasks where the hidden spy saw the needed helper called. Repo tests kept: passing runs whose
    patch also keeps the repository's own test suite green. Judge rank: mean share of the other candidate patches the
    blind judge ranked above this configuration's patches (0 = always best, lower is better). Input, output, seconds:
    medians per run.</p>
  </section>

  <section>
    <div class="eyebrow">Reuse</div>
    <h2>What the model saw of the helper decides whether it reuses it</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Needed helper in context as</th><th class=r>Reused, thinking on</th><th class=r>Reused, thinking off</th></tr></thead>
      <tbody>{vis_rows}</tbody></table></div>
    <p class="prose">With the full source in context the helper is almost always used. With thinking on, a bare signature
    (the L6 repository map) is enough too; with thinking off it mostly wasn't, the model asked to look first instead of
    answering. When the helper isn't in the context at all, the model writes its own version.</p>
  </section>

  <section>
    <div class="eyebrow">Hypotheses</div>
    <h2>What we expected and what happened</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Expectation</th><th>Verdict</th><th>Evidence (final round)</th></tr></thead>
      <tbody>
        <tr><td>Reuse depends on whether the helper's code is visible.</td><td><span class="verdict v-held">held</span></td>
            <td>Full source {vis['full source'][1]}/{vis['full source'][0]}, signature {vis['signature only'][1]}/{vis['signature only'][0]}, not seen {vis['not seen'][1]}/{vis['not seen'][0]}.</td></tr>
        <tr><td>An agent with tools matches the whole repo at a fraction of the input.</td><td><span class="verdict v-held">held</span></td>
            <td>L7 grep {p(l7g)}/{len(l7g)} vs L3 {p(l3)}/{len(l3)}; median fresh input per run {st.median(r['tokens_in'] - (r.get('tokens_cached') or 0) for r in l7g) / 1000:.0f}k vs {st.median(r['tokens_in'] for r in l3) / 1000:.0f}k.</td></tr>
        <tr><td>Model-written search queries beat changing the search engine.</td><td><span class="verdict v-part">partly</span></td>
            <td>With BM25, model queries {p(by.get('L5 bm25 model_queries', []))}/27 vs task text {p(by.get('L5 bm25 task_vs_code', []))}/27; with embeddings the task text did as well ({p(by.get('L5 embed task_vs_code', []))}/27).</td></tr>
        <tr><td>A repository map of signatures is enough to write the change.</td><td><span class="verdict v-part">with thinking</span></td>
            <td>L6 {p(by.get('L6', []))}/27 with thinking, {lv_think['L6'][1]}/{lv_think['L6'][0]} without (thinking off, the model asked to see code instead of answering).</td></tr>
        <tr><td>Thinking matters most when the context is thin.</td><td><span class="verdict v-held">held</span></td>
            <td>Largest gains at L6 ({lv_think['L6'][1]} → {lv_think['L6'][2]}) and L1 ({lv_think['L1'][1]} → {lv_think['L1'][2]}); smallest at L3 ({lv_think['L3'][1]} → {lv_think['L3'][2]}).</td></tr>
        <tr><td>Richer agent tools and a self-managed working set help.</td><td><span class="verdict v-not">not on repo M</span></td>
            <td>rich {p(by.get('L7 rich', []))}/27, selective {p(by.get('L7 selective', []))}/27 vs grep {p(l7g)}/27; M's files are small, so there's little to save (repo L tests this next).</td></tr>
        <tr><td>Documentation drift (the trap) is caught only by levels that show and check the helper's body.</td><td><span class="verdict v-held">held</span></td>
            <td>Reused and fixed only at L4 and L7; the signature-only levels (L6) trusted the docs.</td></tr>
      </tbody></table></div>
  </section>

  <section>
    <div class="eyebrow">Reasoning</div>
    <h2>Thinking on vs thinking off</h2>
    <p class="prose">The same {nc} configurations with and without thinking. Thinking costs about
    {out_on / out_off:.0f}× the output tokens ({out_off:,.0f} → {out_on:,.0f} per run). What it buys is mostly answers
    that work at all: with thinking off a third of the runs produced no usable code or code that doesn't import.
    Those failures are exactly the ones a harness that runs tests would catch and could retry. The silently wrong
    answers, whose repo tests pass, are as common either way. Only tests that cover the behaviour catch those.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Outcome</th><th class=r>Thinking off</th><th class=r>Thinking on</th></tr></thead>
      <tbody>{think_rows}</tbody></table></div>
    <div class="table-wrap"><table>
      <thead><tr><th>Level</th><th class=r>PASS, off</th><th class=r>PASS, on</th><th class=r>Change</th></tr></thead>
      <tbody>{think_lv_rows}</tbody></table></div>
    <p class="small">No level could run code, so nothing told the model it was wrong. With a test loop, thinking off would
    likely close part of the gap, at the cost of retries. Latency isn't compared here: the thinking-off round ran 4
    requests at a time on a warm cache.</p>
  </section>

  <section>
    <div class="eyebrow">Performance</div>
    <h2>Latency</h2>
    <p class="prose">Final round only: one request at a time, cold prefix cache, {list(final.values())[0].get('quant', '')}.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Level</th>{''.join(f'<th class=r>{e(b)}</th>' for b in buckets)}<th class=r>Median s</th><th class=r>Minutes per PASS</th></tr></thead>
      <tbody>{''.join(lat_rows)}</tbody></table></div>
    <p class="small">Minutes per PASS: all time spent at that level divided by its passing runs. Thin-context levels think
    longest (L1 runs often think for 10k+ tokens); the agent is the fastest per pass.</p>
  </section>

  <section>
    <div class="eyebrow">Performance</div>
    <h2>Cost</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>Level</th><th class=r>Input / run</th><th class=r>Cached / run</th><th class=r>Output / run</th>
      <th class=r>Tokens per PASS</th>{''.join(f'<th class=r>$ per 100 PASS, {e(n)}</th>' for n in PRICES)}</tr></thead>
      <tbody>{''.join(cost_rows)}</tbody></table></div>
    <p class="small">Illustrative prices, not quotes: {e(price_note)}. Cached input is counted at the full price here;
    with prompt caching the agent's repeated prefixes (the "cached" column) would cost less.</p>
  </section>

  <section>
    <div class="eyebrow">Agents</div>
    <h2>Richer tools and a self-managed working set</h2>
    <div class="table-wrap"><table>
      <thead><tr><th>L7 variant</th><th class=r>PASS</th><th class=r>Reuse</th><th class=r>Input</th><th class=r>Cached</th>
      <th class=r>Tool calls</th><th class=r>Seconds</th></tr></thead>
      <tbody>{''.join(agents)}</tbody></table></div>
    <p class="prose"><b>grep</b> and <b>hybrid</b>: grep, file reads, references (hybrid adds semantic search).
    <b>rich</b>: the same tools, richer (search hits with source, line ranges, read a function by name).
    <b>selective</b>: rich plus <code>drop</code>, which removes earlier results from the conversation. On repo M the richer
    tools didn't help: the files are small, so reading whole files is cheap, and the model rarely used
    <code>read_symbol</code> ({calls['read_symbol']} calls against {calls['read_file']} file reads). With thinking on,
    {sel_drop_runs} of {len(sel)} selective runs dropped something, but the context saved was small:
    {sel_dropped:,} tokens dropped and {sel_saved:,} input tokens avoided in total, about
    {100 * sel_saved / sel_in:.1f}% of selective's input. Repo L (169k tokens, files and functions hundreds of lines
    long) is built to give these tools something to do.</p>
  </section>

  <section>
    <div class="eyebrow">Quality</div>
    <h2>Judged code quality</h2>
    <p class="prose">A blind judge (agy, rubric v2) compared all candidate patches of a task side by side, without knowing
    how any was made. Its correctness score agrees with the tests almost everywhere:
    {agree[(False, False)]} failing runs got a low score and none a high one; {agree[(True, True)]} passing runs got a
    high one. The {agree[(True, False)]} passing runs it scored low are where the hidden tests miss something.</p>
    <div class="table-wrap"><table>
      <thead><tr><th></th><th class=r>Judge: correctness 4-5</th><th class=r>Judge: 1-3</th></tr></thead>
      <tbody><tr><td>Hidden tests pass</td><td class=r>{agree[(True, True)]}</td><td class=r>{agree[(True, False)]}</td></tr>
      <tr><td>Hidden tests fail</td><td class=r>{agree[(False, True)]}</td><td class=r>{agree[(False, False)]}</td></tr></tbody></table></div>
    <p class="prose">Patch coverage: of {len(passed_cov)} passing patches, {len(low_cov)} had less than 80% of their changed
    statements run by the hidden tests. {len(broke)} passing patches broke the repository's own tests (the regression
    check). No run, at any level, added tests of its own: the prompt never asks for tests ({tests_added} runs did).</p>
  </section>

  <section>
    <div class="eyebrow">Examples</div>
    <h2>How the model fails</h2>
    <div class="gallery">
      <div class="card"><h4>Trusted the documentation</h4><p class="small">trap-001: <code>format_weight</code> documents
      "lb" but returns kg. The repository map showed only the signature and docstring; the patch called it with "lb".</p>
      {link(find('trap-001', 'L6'), 'L6 · trap-001 · M')}</div>
      <div class="card"><h4>Reused and fixed it</h4><p class="small">The agent read the helper, saw the missing branch,
      fixed <code>packaging.py</code> and reused it from the label code.</p>{link(find('trap-001', 'L7 grep'), 'L7 grep · trap-001 · M')}</div>
      <div class="card"><h4>Right fix, unrelated edit</h4><p class="small">Fixed the helper correctly, then gave a dataclass
      field a default for no reason; the module no longer imports.</p>{link(find('trap-001', 'L3'), 'L3 · trap-001 · M')}</div>
      <div class="card"><h4>Corrupted output</h4><p class="small">Correct fix, but duplicated tokens garbled a line
      (<code>format_weight(weight_g(weight_g, …</code>): a syntax error. Seen in about 2% of final-round answers.</p>{link(find('trap-001', 'L7 rich'), 'L7 rich · trap-001 · M')}</div>
      <div class="card"><h4>Passed, but broke the repo</h4><p class="small">Hidden tests pass, the repository's own tests
      don't. {len(broke)} such runs in the final round.</p>{''.join('<br>' + link(r['run_id'], level_label(r) + ' · ' + r['task_id']) for r in broke[:3])}</div>
      <div class="card"><h4>Thought past the output limit</h4><p class="small">With no context at all, the model reasoned
      until it hit the 32k output limit and never wrote the code.</p>{link(oot['run_id'], level_label(oot) + ' · ' + oot['task_id']) if oot else ''}</div>
      <div class="card"><h4>Used dead code</h4><p class="small">The patch imports from the old, unused <code>common/</code>
      package (named like the canonical place). {len(dead)} final-round runs.</p>{''.join('<br>' + link(r['run_id'], level_label(r) + ' · ' + r['task_id']) for r in dead[:2])}</div>
    </div>
  </section>

  <section>
    <div class="eyebrow">Limits</div>
    <h2>How sure we are</h2>
    <ul class="prose">
      <li>One model, one sample per configuration. Repeats in the development round flipped about 1 run in 5, roughly
      ±3 per 13 runs of a configuration: small differences between configurations are noise.</li>
      <li>One synthetic repository (two sizes) written for the experiment; real repositories are messier. Repo L, larger
      and older, comes next.</li>
      <li>One trap task. The judge is an LLM; its scores agree with the tests but it can share the model's blind spots.</li>
      <li>No level could run code. Agents with a test loop would recover some failures (see Thinking on vs off).</li>
    </ul>
  </section>

  <section>
    <div class="next-pages">
      <a href="method"><b>Method</b><span>The levels, the rules that keep the comparison fair, labels and grading.</span></a>
      <a href="tasks"><b>Tasks</b><span>Every task with its text, hidden tests and reference solution.</span></a>
      <a href="data"><b>Raw data</b><span>Every run: prompt by part, thinking, answer, patch and test output, side by side.</span></a>
      <a href="guide"><b>Run it yourself</b><span>What you need: an OpenAI-compatible API, optionally an embedder.</span></a>
    </div>
  </section>
""" + FOOT
    return page


def head_foot():
    """Head, nav and footer from the current page, so the site's header stays in one place."""
    current = (ROOT / "docs" / "index.html").read_text()
    head = current.split("<main>")[0]
    foot = "  <footer>" + current.split("<footer>")[1].split("</footer>")[0] + "</footer>\n</main>\n</body>\n</html>\n"
    return head, foot


HEAD, FOOT = head_foot()

if __name__ == "__main__":
    site = Path(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--out=")), ROOT / "docs"))
    site = site if site.is_absolute() else ROOT / site
    (site / "index.html").write_text(build())
    print(f"wrote {site / 'index.html'}")
