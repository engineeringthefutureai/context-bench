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
from build_site import level_label, pair_key, outcome, thinking_on_rows  # noqa: E402
import site_layout  # noqa: E402

# Illustrative API prices (USD per million tokens), stated on the page. Not quotes.
PRICES = {"open-weights host": (0.20, 0.80), "frontier API": (3.00, 15.00)}
LEVEL_ORDER = ["L0", "L1", "L1 +hint", "L2", "L3", "L4 bm25", "L4 embed", "L5 bm25 task_vs_code",
               "L5 bm25 model_queries", "L5 embed task_vs_code", "L5 embed task_vs_code +hint", "L5 embed model_queries",
               "L6", "L6+docs", "L7 grep", "L7 hybrid", "L7 rich", "L7 selective"]


def lag_last(label: str) -> tuple:
    """Labels outside LEVEL_ORDER sort by name, except L-AG (an external harness, not a context level): last."""
    return (label.startswith("L-AG"), label)


def e(x) -> str:
    return html.escape(str(x))


def load():
    rows = [json.loads(line) for line in open(RESULTS / "results.jsonl")]
    grades = {}
    for line in (open(RESULTS / "grades.jsonl") if (RESULTS / "grades.jsonl").exists() else []):
        g = json.loads(line)
        if str(g.get("rubric_version")) == "2":
            grades[g["run_id"]] = g
    key = pair_key              # one dict per round, so the key leaves the round out and the rounds pair up
    pv = max((r.get("prompt_version", 1) for r in rows), default=3)          # the round's prompt version
    final = {key(r): r for r in thinking_on_rows(rows, pv)}     # thinking on (concurrent; clean only if nothing else)
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
    names = {"pass": "PASS", "fail": "FAIL", "unparsable": "UNPARSABLE", "explore": "ASKED TO EXPLORE",
             "noanswer": "NO ANSWER", "stopped": "STOPPED EARLY", "invalid": "INVALID", "oot": "OUT OF TOKENS",
             "time": "OUT OF TIME", "nofit": "NO FIT", "running": "in progress", "queued": "queued", "retry": "to rerun in the next pass"}
    parts = "".join(f'<span class="o-{k}" style="flex:{counts.get(k, 0)}" title="{names[k]}: {counts.get(k, 0)}"></span>'
                    for k in ("pass", "fail", "unparsable", "explore", "noanswer", "stopped", "invalid", "oot", "time", "nofit", "running", "queued", "retry")
                    if counts.get(k))
    if not_run:
        parts += f'<span class="o-na" style="flex:{not_run}" title="not run at this level: {not_run}"></span>'
    note = f' <span class="small">({not_run} not run at this level)</span>' if not_run else ""
    return (f'<div class="obar {cls}"><span class="lab">{e(label)}</span><span class="track" role="img" '
            f'aria-label="{e(label)}: {counts.get("pass", 0)} of {n} pass, {not_run} not run">{parts}</span>'
            f'<span class="v">{counts.get("pass", 0)}/{n}{note}</span></div>')




PENALTY_POINTS = (("scope", "changes unrelated to the task", -10),
                  ("reimplemented", "re-implements logic the repository already has", -10),
                  ("convention", "breaks a convention visible in the repository (max -10 in total)", -5),
                  ("api", "changes a public signature or behaviour other code relies on", -15),
                  ("wrong_untested", "logic that looks wrong where the tests don't reach", -15),
                  ("risk", "a security or data problem", -20))


def penalty_section(rows: list[dict], title: str, sid: str = "penalties") -> str:
    """Penalty grading for one round, per configuration: passing runs, how many the judge graded, mean
    points per graded run, the share with no penalty, the clean pass rate (passing with no penalty, out of
    all runs) and the most frequent codes. Empty when none of the round's runs is graded."""
    from build_site import load_penalties
    from context_bench.penalty import hard_pass
    pen = load_penalties()
    if not any(r["run_id"] in pen for r in rows):
        return ""
    by = collections.defaultdict(list)
    for r in rows:
        by[level_label(r)].append(r)
    order = [l for l in LEVEL_ORDER if l in by] + sorted(set(by) - set(LEVEL_ORDER), key=lag_last)
    body, graded_all = [], []
    for lab in order:
        rs = by[lab]
        gs = [pen[r["run_id"]] for r in rs if r["run_id"] in pen and hard_pass(r)]
        graded_all += [(r, pen[r["run_id"]]) for r in rs if r["run_id"] in pen and hard_pass(r)]
        passes = sum(map(hard_pass, rs))
        clean = sum(1 for g in gs if not g["penalties"])
        codes = collections.Counter(p["code"] for g in gs for p in g["penalties"])
        top = ", ".join(f"{c} {n}" for c, n in codes.most_common(3)) or "–"
        mean = f"{sum(g['points'] for g in gs) / len(gs):.1f}" if gs else "–"
        body.append(f"<tr><td>{e(lab)}</td><td class=r>{len(rs)}</td><td class=r>{passes}</td><td class=r>{len(gs)}</td>"
                    f"<td class=r>{mean}</td><td class=r>{pct(clean, len(gs))}</td>"
                    f"<td class=r>{pct(clean, len(rs)) if gs and len(gs) == passes else '–'}</td>"
                    f"<td class=small>{e(top)}</td></tr>")
    dist = collections.Counter(g["points"] for _, g in graded_all)
    n = len(graded_all)
    dist_cells = "".join(f"<td class=r>{dist.get(p, 0)}</td>" for p in range(0, -45, -5))
    worst = sorted(graded_all, key=lambda rg: (rg[1]["points"], rg[0]["run_id"]))[:5]
    worst_html = "".join(
        f"<li>{link(r['run_id'], r['task_id'] + ' @' + r['repo_size'] + ', ' + level_label(r))}: {g['points']} "
        f"({', '.join(p['code'] for p in g['penalties'])})</li>" for r, g in worst)
    catalogue = "".join(f"<tr><td><code>{c}</code></td><td>{e(d)}</td><td class=r>{p}</td></tr>" for c, d, p in PENALTY_POINTS)
    intro = (f"""    <p class="prose">The hidden tests decide PASS or FAIL, and the judge never changes that. For each passing
    patch, a blind judge (Gemini 4 Neon in agy) looks for defects the tests wouldn't catch, using only the codes below, and has to
    quote a changed line of the patch for each one. The harness keeps a penalty only if that quote really is a
    changed line. Points range from 0 (nothing found) to the cap of -40. They rate how clean a passing patch is, and
    PASS or FAIL still comes from the tests alone. Failing runs aren't graded.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Code</th><th>Defect</th><th class=r>Points</th></tr></thead>
      <tbody>{catalogue}</tbody></table></div>
""" if sid == "penalties" else
             '    <p class="prose">The same judge, codes and points as for thinking off (see above), on the passing runs '
             'of the thinking-on round.</p>\n')
    return f"""
  <section id="{sid}">
    <h2>Judged code quality: penalties ({e(title)})</h2>
{intro}    <div class="table-wrap"><table>
      <thead><tr><th>Configuration</th><th class=r>Runs</th><th class=r>PASS</th><th class=r>Graded</th>
      <th class=r>Mean points</th><th class=r>No penalty</th><th class=r>Clean pass</th><th>Top codes</th></tr></thead>
      <tbody>{''.join(body)}</tbody></table></div>
    <p class="small">PASS here also requires the repository's own tests to stay green. Graded: passing runs the
    judge has graded. Mean points and No penalty are over the graded runs. Clean pass: runs that passed with no
    penalty, out of all runs, given once every passing run of the configuration is graded.</p>
    <h3>Points per graded run</h3>
    <div class="table-wrap"><table>
      <thead><tr>{''.join(f'<th class=r>{p}</th>' for p in range(0, -45, -5))}<th class=r>Graded</th></tr></thead>
      <tbody><tr>{dist_cells}<td class=r>{n}</td></tr></tbody></table></div>
    <h3>Most penalized</h3>
    <ul class="small">{worst_html}</ul>
  </section>"""


def sweep_status() -> dict | None:
    """results/sweep_status.json (written by cbench run), only while its sweep process is alive."""
    path = RESULTS / "sweep_status.json"
    try:
        st_ = json.loads(path.read_text())
        os.kill(int(st_["pid"]), 0)
        return st_
    except (OSError, ValueError, KeyError):
        return None


def expected_runs(rows: list[dict]) -> collections.Counter:
    """(round, configuration label) -> runs the round is meant to have, from config/experiment.toml (the model
    the recorded rows use; L5 modes that aren't implemented left out, as the sweep skips them). Tells a cell
    still to run (a run a server error pushed to the sweep's next pass) from one a level doesn't run by design."""
    out = collections.Counter()
    try:
        from context_bench.runner import Experiment
        from dataclasses import asdict
        exp = Experiment(ROOT / "config" / "experiment.toml")
        models = collections.Counter(r.get("model") for r in rows).most_common(1)
        if not models:
            return out
        for thinking, rnd in ((False, "off"), (True, "final")):
            for c in exp.configs(models=[models[0][0]], thinking=thinking):
                if c.level == "L5" and c.query_mode in ("task_vs_summary", "draft_then_search"):
                    continue
                out[rnd, level_label(asdict(c))] += 1
    except Exception:            # the page still renders from what is recorded
        pass
    return out


def build_progress(final: dict, off: dict) -> str:
    """While a round is in progress (no final-round runs yet): per configuration, a bar of the outcomes
    recorded so far plus what the running sweep has in progress and queued; then outcomes per repo."""
    status = sweep_status()
    expected = expected_runs(list(final.values()) + list(off.values()))
    pending = collections.defaultdict(collections.Counter)      # (round, label) -> running / queued
    for r in (status or {}).get("runs", {}).values():
        pending["final" if r.get("thinking") else "off", level_label(r)][r["state"]] += 1

    def bars(rows, rnd, title):
        """One bar per configuration (all repos together), on a common scale: recorded outcomes plus what
        the running sweep has in progress and queued."""
        by = collections.defaultdict(list)
        for r in rows:
            by[level_label(r)].append(r)
        labels = set(by) | {lab for (x, lab) in pending if x == rnd}
        if not labels:
            return f"<h3>{e(title)}</h3><p class=small>Nothing recorded or queued yet.</p>"
        order = [l for l in LEVEL_ORDER if l in labels] + sorted(labels - set(LEVEL_ORDER), key=lag_last)
        retry = {lab: max(expected[rnd, lab] - len(by[lab]) - sum(pending[rnd, lab].values()), 0) for lab in order}
        totals = {lab: len(by[lab]) + sum(pending[rnd, lab].values()) + retry[lab] for lab in order}
        scale = max(totals.values())
        out = []
        for i, lab in enumerate(order):
            oc = collections.Counter(outcome(r) for r in by[lab])
            pend = pending[rnd, lab]
            counts = {"pass": oc["PASS"], "fail": oc["FAIL"], "unparsable": oc["UNPARSABLE"], "explore": oc["ASKED TO EXPLORE"],
                      "noanswer": oc["NO ANSWER"], "stopped": oc["STOPPED EARLY"], "invalid": oc["INVALID"],
                      "oot": oc["OUT OF TOKENS"], "time": oc["OUT OF TIME"], "nofit": oc["NO FIT"], "running": pend["running"], "queued": pend["queued"], "retry": retry[lab]}
            group = lab.split()[0].replace("+docs", "")
            prev = order[i - 1].split()[0].replace("+docs", "") if i else group
            bar = obar(lab, counts, totals[lab], "group-start" if group != prev else "", scale=scale)
            extra = " · ".join(x for x in (f"{pend['running']} running" if pend["running"] else "",
                                            f"{pend['queued']} queued" if pend["queued"] else "",
                                            f"{retry[lab]} to rerun" if retry[lab] else "") if x)
            if extra:
                bar = bar.replace('</span></div>', f' <span class="small">{extra}</span></span></div>', 1)
            out.append(bar)
        return f"<h3>{e(title)}</h3><div class=obars>{''.join(out)}</div>"

    def table(rows, title):
        by = collections.defaultdict(collections.Counter)
        for r in rows:
            by[r["repo_size"]][outcome(r)] += 1
        cols = ["PASS", "FAIL", "UNPARSABLE", "ASKED TO EXPLORE", "NO ANSWER", "STOPPED EARLY", "INVALID", "OUT OF TOKENS",
                "OUT OF TIME", "NO FIT"]
        body = "".join(f"<tr><td>{s}</td>" + "".join(f"<td class=r>{c[k]}</td>" for k in cols)
                       + f"<td class=r>{sum(c.values())}</td></tr>" for s, c in sorted(by.items()))
        return (f"<h3>{e(title)}</h3><div class=table-wrap><table><thead><tr><th>Repo</th>"
                + "".join(f"<th class=r>{k}</th>" for k in cols) + "<th class=r>Runs</th></tr></thead>"
                f"<tbody>{body or '<tr><td colspan=12>none yet</td></tr>'}</tbody></table></div>")

    legend = ('<div class="olegend small">' + "".join(
        f'<span><i class="o-{k}"></i>{n}</span>' for k, n in (("pass", "PASS"), ("fail", "FAIL"),
                                                          ("unparsable", "UNPARSABLE"), ("explore", "ASKED TO EXPLORE"), ("noanswer", "NO ANSWER"), ("stopped", "STOPPED EARLY"),
                                                          ("invalid", "INVALID"), ("oot", "OUT OF TOKENS"), ("time", "OUT OF TIME"), ("nofit", "NO FIT"),
                                                          ("running", "in progress"), ("queued", "queued"),
                                                          ("retry", "to rerun next pass"), ("na", "not run at this level"))) + "</div>")
    if status:
        runs = status.get("runs", {}).values()
        live = (f'<p class="small">Sweep running since {e(status.get("started_at", "")[:16].replace("T", " "))} UTC: '
                f'{sum(r["state"] == "running" for r in runs)} in progress, {sum(r["state"] == "queued" for r in runs)} '
                f'queued (last update {e(status.get("updated_at", "")[11:16])} UTC).</p>')
    else:
        live = '<p class="small">No sweep is running right now.</p>'
    return _page(f"""
  <header style="display:grid;gap:12px">
    <div class="eyebrow">Round in progress · preview</div>
    <h1>How context building affects generated code</h1>
    <p class="lede">The same model gets the same coding tasks, and only the way the harness builds its context
    varies, with deliberately simple builders. Each level is one primitive component of a coding harness, and none
    of them is a complete agentic harness. This page shows how much even such a primitive implementation moves the results.</p>
    <p class="small">The current round is still running, and this page shows what the harness has recorded so far. The
    full write-up will follow when the thinking-on round is finished (clean-mode cost and latency figures are
    reported separately). The runs can be browsed in the raw-data viewer.</p>
  </header>
  <section>
    <h2>Per configuration</h2>
    {live}
    {legend}
    {bars(list(off.values()), "off", "Thinking off")}
    {bars(list(final.values()), "final", "Thinking on")}
    <p class="small">Each bar is one configuration, all repos together, on a common scale that counts the runs
    recorded plus the ones the running sweep still has queued, and the ones a server error moved to its next pass (to rerun). Hatched slots are tasks a level doesn't run by design (L0: control tasks only; L1-L2: each task once, on its smallest repo). The number is PASS out of that total. The Method page defines the
    outcomes.</p>
  </section>
  {penalty_section(list(off.values()), "thinking off")}
  {penalty_section(list(final.values()), "thinking on", "penalties-on")}
  <section>
    <h2>Per repo</h2>
    {table(list(off.values()), "Thinking off")}
    {table(list(final.values()), "Thinking on")}
  </section>
""")


def build() -> str:
    final, off, grades = load()
    if sweep_status():
        return build_progress(final, off)    # a sweep is running: the live preview
    return build_final(final, off)


# Level groups for the overview charts: (group, what the model gets). Arms that change the prompt (+hint) or the
# tool names (Qwen names) are left out of the group rates; the configuration tables list them.
GROUPS = [("L1", "the target file"), ("L2", "target, its imports and its test"), ("L3", "the whole repository"),
          ("L4", "3 files found by search"), ("L5", "8 functions found by search"), ("L6", "a map of every signature"),
          ("L7", "an agent with search tools"), ("L8", "an agent that reads by symbol and edits in place")]


def in_group(r: dict, g: str) -> bool:
    return r["level"] == g and (r.get("prompt_arm") or "base") in ("base", "edit_tools") and (r.get("tool_names") or "harness") == "harness"


def sent(r: dict) -> bool:
    """False for a run whose prompt never reached the model (NO FIT before the first request): its recorded
    tokens_in is the prompt's size, not tokens spent."""
    return not (outcome(r) == "NO FIT" and not r.get("tokens_out"))


def passed(xs) -> int:
    return sum(outcome(r) == "PASS" for r in xs)


def dumbbell(rows: list[tuple[str, str, float, float, str]]) -> str:
    """Pass rate per row, thinking off (hollow) to thinking on (filled), on one 0-100% axis. rows: (label,
    sublabel, off %, on %, note). Inline SVG coloured by the site's tokens, so it follows the theme."""
    w, left, right, rh, top = 760, 250, 96, 34, 28
    x = lambda p: left + (w - left - right) * p / 100
    h = top + rh * len(rows) + 8
    grid = "".join(f'<line x1="{x(p):.1f}" y1="{top - 6}" x2="{x(p):.1f}" y2="{h - 8}" style="stroke:var(--rule)"/>'
                   f'<text x="{x(p):.1f}" y="{top - 12}" text-anchor="middle" class="ax">{p}%</text>' for p in range(0, 101, 25))
    body = []
    for i, (lab, sub, a, b, note) in enumerate(rows):
        y = top + rh * i + rh / 2
        ref = lab.startswith("L-AG")
        col = "var(--muted)" if ref else "var(--accent)"
        body.append(
            f'<text x="0" y="{y - 2:.1f}" class="lb">{e(lab)}</text><text x="0" y="{y + 12:.1f}" class="sb">{e(sub)}</text>'
            f'<line x1="{x(a):.1f}" y1="{y:.1f}" x2="{x(b):.1f}" y2="{y:.1f}" style="stroke:{col};stroke-width:2;opacity:.45"/>'
            f'<circle cx="{x(a):.1f}" cy="{y:.1f}" r="5.5" style="fill:var(--surface);stroke:{col};stroke-width:2"><title>{e(lab)}, thinking off: {a:.0f}%</title></circle>'
            f'<circle cx="{x(b):.1f}" cy="{y:.1f}" r="6" style="fill:{col}"><title>{e(lab)}, thinking on: {b:.0f}%</title></circle>'
            f'<text x="{w - right + 10}" y="{y + 4:.1f}" class="vl">{a:.0f} → <tspan style="font-weight:700">{b:.0f}%</tspan></text>'
            + (f'<text x="{x(max(a, b)) + 12:.1f}" y="{y + 4:.1f}" class="nt">{e(note)}</text>' if note else ""))
    return (f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="Pass rate per level, thinking off and on">'
            f'{grid}{"".join(body)}</svg>')


def hbars(rows: list[tuple[str, list[tuple[float, str, str]], str]], scale: float) -> str:
    """Horizontal bars in the site's .obar layout: (label, [(value, css colour, title)], value text)."""
    out = []
    for lab, segs, val in rows:
        spans = "".join(f'<span style="flex:0 0 {100 * v / scale:.2f}%;background:{c}" title="{e(t)}"></span>' for v, c, t in segs if v)
        out.append(f'<div class="obar"><span class="lab">{e(lab)}</span><span class="track">{spans}</span><span class="v">{val}</span></div>')
    return f'<div class="obars">{"".join(out)}</div>'


def build_final(final: dict, off: dict) -> str:
    """The Results page of the v4 round: both rounds, repos S, M and L; L-AG only as an outside reference."""
    from build_site import load_penalties
    from context_bench.penalty import hard_pass
    pen = load_penalties()
    F = [r for r in final.values() if r["level"] != "L-AG"]
    O = [r for r in off.values() if r["level"] != "L-AG"]
    lag_on = [r for r in final.values() if r["level"] == "L-AG"]
    lag_off = [r for r in off.values() if r["level"] == "L-AG"]
    n_runs = len(final) + len(off)
    rate = lambda xs: 100 * passed(xs) / len(xs) if xs else 0
    G_on = {g: [r for r in F if in_group(r, g)] for g, _ in GROUPS}
    G_off = {g: [r for r in O if in_group(r, g)] for g, _ in GROUPS}

    # ---- the ladder (dumbbell)
    notes = {"L3": "no fit on repo L"}
    ladder = [(g, sub, rate(G_off[g]), rate(G_on[g]), notes.get(g, "")) for g, sub in GROUPS]
    ladder.append(("L-AG", "Antigravity harness, same Qwen model", rate(lag_off), rate(lag_on), ""))

    # ---- repo size heat table (thinking on)
    def cell(xs):
        if not xs:
            return '<div class="hc na">–</div>'
        p = rate(xs)
        nofit = all(outcome(r) == "NO FIT" for r in xs)
        bg = "var(--subtle)" if nofit else f"color-mix(in srgb, var(--good) {p * 0.8:.0f}%, var(--surface))"
        fg = "var(--muted)" if nofit else "#fff" if p >= 60 else "var(--ink)"
        return (f'<div class="hc" style="background:{bg};color:{fg}" title="{passed(xs)} of {len(xs)} pass">'
                f'{"no fit" if nofit else f"{p:.0f}%"}<small>{passed(xs)}/{len(xs)}</small></div>')
    heat_rows = ('<div class="hh"></div><div class="hh">Repo S</div><div class="hh">Repo M</div><div class="hh">Repo L</div>'
                 + "".join(f'<div class="hl"><b>{g}</b><span>{e(sub)}</span></div>'
                           + "".join(cell([r for r in G_on[g] if r["repo_size"] == s]) for s in "SML") for g, sub in GROUPS))

    # ---- reuse by visibility
    def vis_counts(rows):
        v = collections.defaultdict(lambda: [0, 0])
        for r in rows:
            if r.get("reuse_passed") is None:
                continue
            k = ("its full source" if r.get("helper_body_in_context") else "a call site" if r.get("helper_usage_in_context")
                 else "its signature only" if r.get("helper_in_context") else "nothing of it")
            v[k][0] += 1
            v[k][1] += bool(r["reuse_passed"])
        return v
    v_on, v_off = vis_counts(F), vis_counts(O)
    VIS = ["its full source", "a call site", "its signature only", "nothing of it"]
    reuse_rows = []
    for k in VIS:
        for tag, v, col in (("on", v_on, "var(--accent)"), ("off", v_off, "color-mix(in srgb, var(--accent) 45%, var(--surface))")):
            n, y = v[k]
            reuse_rows.append((f"{k.replace('its ', '').replace(' only', '')} · {tag}", [(100 * y / n if n else 0, col, f"{y} of {n} reused")], f"{pct(y, n)} <span class=small>of {n}</span>"))
    vf = v_on["its full source"]
    vn = v_on["nothing of it"]

    # ---- thinking, paired
    common = [k for k in final if k in off and final[k]["level"] != "L-AG"]
    p_off = sum(outcome(off[k]) == "PASS" for k in common)
    p_on = sum(outcome(final[k]) == "PASS" for k in common)
    one_shot = lambda k: final[k]["level"] in ("L1", "L2", "L3", "L4", "L5", "L6")
    agentic = lambda k: final[k]["level"] in ("L7", "L8")
    os_n, ag_n = sum(map(one_shot, common)), sum(map(agentic, common))
    os_off = sum(outcome(off[k]) == "PASS" for k in common if one_shot(k))
    os_on = sum(outcome(final[k]) == "PASS" for k in common if one_shot(k))
    ag_off = sum(outcome(off[k]) == "PASS" for k in common if agentic(k))
    ag_on = sum(outcome(final[k]) == "PASS" for k in common if agentic(k))
    lv_pair = collections.defaultdict(lambda: [0, 0, 0])
    for k in common:
        a = lv_pair[final[k]["level"]]
        a[0] += 1
        a[1] += outcome(off[k]) == "PASS"
        a[2] += outcome(final[k]) == "PASS"

    # ---- tokens
    tot = lambda xs, f: sum((r.get(f) or 0) for r in xs if sent(r))
    in_on, out_on, in_off, out_off = tot(F, "tokens_in"), tot(F, "tokens_out"), tot(O, "tokens_in"), tot(O, "tokens_out")
    big = max(in_on + out_on, in_off + out_off)
    round_bars = hbars([
        ("Thinking off", [(in_off, "var(--accent)", f"input {in_off / 1e6:.1f}M"), (out_off, "var(--warn)", f"output {out_off / 1e6:.1f}M")],
         f"{(in_off + out_off) / 1e6:.0f}M"),
        ("Thinking on", [(in_on, "var(--accent)", f"input {in_on / 1e6:.1f}M"), (out_on, "var(--warn)", f"output {out_on / 1e6:.1f}M")],
         f"{(in_on + out_on) / 1e6:.0f}M")], big)
    tpp = []
    for g, sub in GROUPS:
        xs = G_on[g]
        p = passed(xs)
        tpp.append((g, (tot(xs, "tokens_in") + tot(xs, "tokens_out")) / p if p else 0, tot(xs, "tokens_in") / p if p else 0, sub))
    lag_p = passed(lag_on)
    lag_tpp = (tot(lag_on, "tokens_in") + tot(lag_on, "tokens_out")) / lag_p if lag_p else 0
    tmax = max(t for _, t, _, _ in tpp)
    tpp_bars = hbars([(f"{g}", [(ti, "var(--accent)", f"input {ti / 1e3:.0f}k per PASS"), (t - ti, "var(--warn)", f"output {(t - ti) / 1e3:.0f}k per PASS")],
                       f"{t / 1e3:.0f}k") for g, t, ti, _ in tpp], tmax)
    tok_rows = []
    for g, sub in GROUPS:
        a, b = G_off[g], G_on[g]
        med = lambda xs, f: st.median((r.get(f) or 0) for r in xs if sent(r)) / 1e3 if xs else 0
        tok_rows.append(f"<tr><td>{g}</td><td class=r>{med(a, 'tokens_in'):.1f}k</td><td class=r>{med(b, 'tokens_in'):.1f}k</td>"
                        f"<td class=r>{med(a, 'tokens_out'):.1f}k</td><td class=r>{med(b, 'tokens_out'):.1f}k</td>"
                        f"<td class=r>{med(b, 'peak_ctx_tokens'):.1f}k</td></tr>")
    l7_in = (st.median(r["tokens_in"] for r in G_off["L7"]) / 1e3, st.median(r["tokens_in"] for r in G_on["L7"]) / 1e3)
    ret_tpp = min(t for g, t, _, _ in tpp if g in ("L4", "L5"))
    l3_tpp = next(t for g, t, _, _ in tpp if g == "L3")
    l7_tpp = next(t for g, t, _, _ in tpp if g == "L7")
    l8_tpp = next(t for g, t, _, _ in tpp if g == "L8")
    usd = lambda tin, tout, k: (tin * PRICES[k][0] + tout * PRICES[k][1]) / 1e6

    # ---- code quality (thinking on): clean pass = passes, keeps the repo tests, judge found nothing
    q_rows, clean_rate = [], {}
    for g, sub in GROUPS:
        xs = G_on[g]
        hp = [r for r in xs if hard_pass(r)]
        clean = sum(1 for r in hp if r["run_id"] in pen and not pen[r["run_id"]]["penalties"])
        clean_rate[g] = 100 * clean / len(xs)
        q_rows.append((g, [(100 * clean / len(xs), "var(--good)", f"{clean} clean passes"),
                           (100 * (len(hp) - clean) / len(xs), "color-mix(in srgb, var(--good) 35%, var(--surface))",
                            f"{len(hp) - clean} passes with a judged defect")],
                       f"{100 * clean / len(xs):.0f}% <span class=small>clean</span>"))

    # ---- hypotheses evidence
    by_cfg = collections.defaultdict(list)
    for r in F:
        by_cfg[level_label(r)].append(r)
    by_cfg_off = collections.defaultdict(list)
    for r in O:
        by_cfg_off[level_label(r)].append(r)
    pc = lambda lab, d=by_cfg: f"{passed(d.get(lab, []))}/{len(d.get(lab, []))}"
    sm = lambda g: [r for r in G_on[g] if r["repo_size"] in "SM"]
    mmed = lambda g: st.median(r["tokens_in"] for r in G_on[g] if r["repo_size"] == "M") / 1e3
    lr = lambda g: [r for r in G_on[g] if r["repo_size"] == "L"]
    tail = [r for r in list(final.values()) + list(off.values()) if r.get("tail_check") and r["level"] != "L-AG"]
    tail_pass = sum(outcome(r) == "PASS" for r in tail)
    trap = {(level_label(r), r["repo_size"]): r for r in F if r["task_id"] == "trap-001"}
    trap_fixed = sorted({lab.split()[0] for (lab, s), r in trap.items() if outcome(r) == "PASS" and r.get("reuse_passed")})
    hyp = [
        ("The model reuses a helper when it can see the helper's code.", "held",
         f"Reused with its full source in context: {vf[1]}/{vf[0]}. With nothing of it in context: {vn[1]}/{vn[0]}."),
        ("An agent does as well as the whole repository, and keeps working when the repository no longer fits.", "held",
         f"Repos S and M: L3 {passed(sm('L3'))}/{len(sm('L3'))}, L7 {passed(sm('L7'))}/{len(sm('L7'))}. Repo L: L3 doesn't fit, L7 {passed(lr('L7'))}/{len(lr('L7'))}. On repo M both use about the same input with thinking on (median {mmed('L3'):.0f}k for L3, {mmed('L7'):.0f}k for L7)."),
        ("Search queries written by the model beat searching with the task text.", "partly",
         f"BM25: model queries {pc('L5 bm25 model_queries')} against task text {pc('L5 bm25 task_vs_code')}. Embeddings: {pc('L5 embed model_queries')} against {pc('L5 embed task_vs_code')}."),
        ("A map of signatures is enough to write the change.", "with thinking",
         f"Thinking on: L6 {pc('L6')}, with docstrings {pc('L6+docs')}. Thinking off: {pc('L6', by_cfg_off)} and {pc('L6+docs', by_cfg_off)}."),
        ("Thinking helps most where the context is thinnest.", "partly",
         f"The largest gain is at the signature map (L6 {lv_pair['L6'][1]} → {lv_pair['L6'][2]} of {lv_pair['L6'][0]}). Retrieval gains 20 to 25 points, the agents {round(100 * (lv_pair['L7'][2] - lv_pair['L7'][1]) / lv_pair['L7'][0])}. With only the target file (L1) the rate stays low: {lv_pair['L1'][1]} → {lv_pair['L1'][2]} of {lv_pair['L1'][0]}."),
        ("Richer search tools and a self-managed working set help on a large repository.", "small",
         f"Thinking on: grep {pc('L7 grep')}, hybrid {pc('L7 hybrid')}, rich {pc('L7 rich')}, selective {pc('L7 selective')}. The differences are within the noise of one sample."),
        ("Reading code by symbol instead of whole files saves tokens.", "not held",
         f"L8 reads by symbol and edits in place. It reached the highest pass rate ({pc('L8')}) but used more input per run than L7 (median {st.median(r['tokens_in'] for r in G_on['L8']) / 1e3:.0f}k against {l7_in[1]:.0f}k)."),
        ("Documentation drift (the trap task) is caught only where the helper's body is in view.", "held",
         f"Thinking on, the model reused and fixed the helper at {', '.join(trap_fixed)}, and failed at L6 on both repos, where only the signature and docstring are in view."),
        ("A single feedback loop can make a huge difference.", "held",
         f"{len(tail)} agent runs ended with a file that didn't parse or failing repository tests and got one more turn with the error. {tail_pass} of them pass now."),
        ("A reuse hint in the prompt raises reuse.", "small",
         f"Thinking on: L1 {pc('L1')} → {pc('L1 +hint')}, L5 embed {pc('L5 embed task_vs_code')} → {pc('L5 embed task_vs_code +hint')}. Thinking off, the hint scores one run lower in both."),
    ]
    vcls = {"held": "v-held", "partly": "v-part", "with thinking": "v-part", "small": "v-part", "not held": "v-not", "no effect": "v-not"}
    hyp_rows = "".join(f"<tr><td>{e(h)}</td><td><span class='verdict {vcls[v]}'>{e(v)}</span></td><td class=small>{e(ev)}</td></tr>"
                       for h, v, ev in hyp)

    # ---- configuration tables (collapsed)
    main = lambda D: [r for r in D if (r.get("tool_names") or "harness") == "harness"]   # the side arm has its own section

    def cfg_table(D):
        by = collections.defaultdict(list)
        for r in main(D):
            by[level_label(r)].append(r)
        order = [l for l in LEVEL_ORDER if l in by] + sorted(set(by) - set(LEVEL_ORDER), key=lag_last)
        rows = []
        for lab in order:
            xs = by[lab]
            reuse = [r["reuse_passed"] for r in xs if r.get("reuse_passed") is not None]
            rows.append(f"<tr><td>{e(lab)}</td><td class=r>{len(xs)}</td><td class=r>{passed(xs)} ({pct(passed(xs), len(xs))})</td>"
                        + "".join(f"<td class=r>{passed([r for r in xs if r['repo_size'] == s])}/{sum(r['repo_size'] == s for r in xs)}</td>" for s in "SML")
                        + f"<td class=r>{(str(sum(reuse)) + '/' + str(len(reuse))) if reuse else '–'}</td>"
                        f"<td class=r>{st.median(r['tokens_in'] for r in xs) / 1e3:.1f}k</td><td class=r>{st.median(r['tokens_out'] for r in xs) / 1e3:.1f}k</td></tr>")
        return ("<div class=table-wrap><table><thead><tr><th>Configuration</th><th class=r>Runs</th><th class=r>PASS</th>"
                "<th class=r>S</th><th class=r>M</th><th class=r>L</th><th class=r>Reuse</th><th class=r>Input</th><th class=r>Output</th></tr></thead>"
                f"<tbody>{''.join(rows)}</tbody></table></div>")

    side = []
    for base, alt in (("L7 selective", "L7 selective qwen names"), ("L8", "L8 qwen names")):
        for lab in (base, alt):
            xs = by_cfg_off.get(lab, [])
            hp = [r for r in xs if hard_pass(r)]
            side.append(dict(lab=lab, names="Qwen's names" if lab == alt else "harness names", n=len(xs), p=passed(xs),
                             clean=sum(1 for r in hp if r["run_id"] in pen and not pen[r["run_id"]]["penalties"]),
                             errs=sum(r.get("tool_errors") or 0 for r in xs),
                             err_runs=sum(1 for r in xs if r.get("tool_errors")),
                             calls=st.median((r.get("tool_calls") or 0) for r in xs) if xs else 0,
                             tin=st.median(r["tokens_in"] for r in xs) / 1e3 if xs else 0))
    side_bars = hbars([(f"{d['lab'].replace(' qwen names', '')} · {d['names']}",
                        [(100 * d["p"] / d["n"] if d["n"] else 0, "var(--accent)" if "Qwen" in d["names"] else "color-mix(in srgb, var(--accent) 45%, var(--surface))", f"{d['p']} of {d['n']} pass")],
                        f"{d['p']}/{d['n']}") for d in side], 100)
    side_rows = "".join(f"<tr><td>{e(d['lab'].replace(' qwen names', ''))}</td><td>{e(d['names'])}</td><td class=r>{d['p']}/{d['n']}</td>"
                        f"<td class=r>{d['clean']}</td><td class=r>{d['errs']}</td><td class=r>{d['err_runs']}</td>"
                        f"<td class=r>{d['calls']:.0f}</td><td class=r>{d['tin']:.0f}k</td></tr>" for d in side)
    s7, q7, s8, q8 = side
    lag_l78 = max(passed(by_cfg[l]) for l in ("L7 rich", "L7 selective", "L8"))
    lag_in = st.median(r["tokens_in"] for r in lag_on) / 1e3
    l8_in = st.median(r["tokens_in"] for r in G_on["L8"]) / 1e3

    # L-AG as a baseline: Antigravity with Gemini 3.5 Flash-Lite (results/gemini-l-ag.jsonl) and with Qwen
    def empty_cmds(xs):
        e = n = 0
        for r in xs:
            pth = RESULTS / "runs" / r["run_id"] / "transcript.jsonl"
            if not pth.exists():
                continue
            for line in pth.open():
                for c in json.loads(line).get("tool_calls") or []:
                    if c.get("name") == "run_command":
                        a = c.get("args") if isinstance(c.get("args"), dict) else {}
                        n += 1
                        e += not str(a.get("command_line", "")).strip()
        return e, n
    gem_latest = {}
    gem_file = RESULTS / "gemini-l-ag.jsonl"
    for line in (gem_file.read_text().splitlines() if gem_file.exists() else []):
        r = json.loads(line)
        gem_latest[(r["task_id"], r["repo_size"])] = r
    gem = list(gem_latest.values())
    gem_miss = sorted(f"{r['task_id']}@{r['repo_size']}" for r in gem if outcome(r) != "PASS")
    med_in = lambda xs: st.median(r["tokens_in"] for r in xs) / 1e3 if xs else 0
    base_rows = []
    for name, xs in (("Antigravity + Gemini 3.5 Flash-Lite", gem), ("Antigravity + Qwen 3.8 27B, thinking on", lag_on),
                     ("Antigravity + Qwen 3.8 27B, thinking off", lag_off)):
        ec, nc = empty_cmds(xs)
        base_rows.append(f"<tr><td>{e(name)}</td><td class=r>{passed(xs)}/{len(xs)}</td>"
                         f"<td class=r>{ec} of {nc}</td><td class=r>{med_in(xs):.0f}k</td></tr>")
    for name, lab in (("L8 + Qwen 3.8 27B, thinking on", "L8"), ("L7 selective + Qwen 3.8 27B, thinking on", "L7 selective")):
        xs = by_cfg.get(lab, [])
        base_rows.append(f"<tr><td>{name}</td><td class=r>{passed(xs)}/{len(xs)}</td><td class=r>–</td>"
                         f"<td class=r>{med_in(xs):.0f}k</td></tr>")
    gem_e, gem_n = empty_cmds(gem)

    flow = "".join(f'<div class="fstep"><b>{a}</b><span>{b}</span></div>'
                   for i, (a, b) in enumerate([("Task", "a change request in product language"),
                                               ("Context builder", "one level, L0 to L8"),
                                               ("Model", "Qwen 3.8 27B, thinking off or on"),
                                               ("Patch", "files written, or edits in place"),
                                               ("Checks", "hidden tests, reuse spy, repo tests, blind judge")]))
    page = _page(f"""
  <style>
    .chart {{ width: 100%; height: auto; font-family: var(--font-data); }}
    .chart .ax {{ font-size: 11px; fill: var(--muted); }} .chart .lb {{ font-size: 13px; font-weight: 600; fill: var(--ink); }}
    .chart .sb {{ font-size: 10.5px; fill: var(--muted); }} .chart .vl {{ font-size: 12px; fill: var(--ink); }}
    .chart .nt {{ font-size: 10.5px; fill: var(--muted); font-style: italic; }}
    .chart-legend {{ display: flex; gap: 18px; flex-wrap: wrap; font-size: 0.85rem; color: var(--muted); }}
    .chart-legend i {{ display: inline-block; width: 11px; height: 11px; border-radius: 50%; margin-right: 6px; vertical-align: -1px; }}
    .flow {{ display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 18px; }}
    .fstep {{ position: relative; background: var(--surface); border: 1px solid var(--rule); border-radius: 8px; padding: 10px 12px; display: grid; gap: 2px; align-content: start; }}
    .fstep:not(:last-child)::after {{ content: "→"; position: absolute; right: -15px; top: 50%; transform: translateY(-50%); color: var(--muted); font-family: var(--font-data); }}
    .heat {{ display: grid; grid-template-columns: minmax(0, 1.6fr) repeat(3, minmax(0, 1fr)); gap: 4px; }}
    .heat .hh {{ font: 600 0.72rem var(--font-data); text-transform: uppercase; letter-spacing: 0.05em; color: var(--muted); text-align: center; padding-bottom: 2px; }}
    .heat .hl {{ display: grid; align-content: center; padding: 6px 4px; }} .heat .hl b {{ font-family: var(--font-data); }}
    .heat .hl span {{ font-size: 0.8rem; color: var(--muted); line-height: 1.3; }}
    .heat .hc {{ display: grid; place-content: center; text-align: center; border-radius: 6px; padding: 8px 4px; font: 700 1rem var(--font-data); }}
    .heat .hc small {{ font-weight: 400; font-size: 0.7rem; opacity: 0.85; }} .heat .na {{ color: var(--muted); }}
    .callout .small {{ margin-top: 10px; }}
    section h3 {{ margin: 18px 0 6px; }}
    .fstep b {{ font-family: var(--font-display); font-size: 0.9rem; }} .fstep span {{ font-size: 0.82rem; color: var(--muted); }}
    .callout {{ border-left: 4px solid var(--muted); background: var(--subtle); padding: 12px 16px; border-radius: 0 8px 8px 0; }}
    .twocol {{ display: grid; gap: 18px; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); }}
    details.more > summary {{ cursor: pointer; font-family: var(--font-data); font-size: 0.9rem; color: var(--accent); }}
    @media (max-width: 640px) {{ .flow {{ grid-template-columns: 1fr 1fr; }} .fstep::after {{ display: none; }} }}
  </style>
  <header style="display:grid;gap:12px">
    <div class="eyebrow">Experiment report · prompt v4 round</div>
    <h1>How context building affects generated code</h1>
    <p class="lede">One model gets the same 30 coding tasks every time. Only the context changes. The
    harness builds it in deliberately simple ways, from a single file up to a minimal agent that explores the repository
    with search tools of its own, and each of those ways is one primitive part of a coding harness. This page reports how far
    that one choice moved the results in this experiment, and what it cost in tokens.</p>
    <div class="meta"><span>Qwen 3.8 27B FP8 on vLLM</span><span>30 tasks on repos S, M and L</span><span>{n_runs:,} runs, thinking off and on</span></div>
    <div class="flow" aria-label="How one run works">{flow}</div>
  </header>

  <section class="takeaways breakout" aria-label="Key findings">
    <div class="takeaway"><b>Agents lifted results to a new level</b><span>On these tasks L7 and L8 passed {rate(G_on['L7']):.0f}% and {rate(G_on['L8']):.0f}% with thinking on, on every repo size. The best search-based level reached {max(rate(G_on[g]) for g in ('L4', 'L5', 'L6')):.0f}%.</span></div>
    <div class="takeaway"><b>Context building was the key to higher-quality code</b><span>Clean passes rose from {clean_rate['L1']:.0f}% with one file (L1) to {clean_rate['L8']:.0f}% at L8. Helper reuse went from {pct(vn[1], vn[0])} with the helper out of view to {pct(vf[1], vf[0])} with its source in context.</span></div>
    <div class="takeaway"><b>Thinking lifted one-shot prompts much more than agents</b><span>Same runs, off → on: the one-shot levels L1 to L6 went from {pct(os_off, os_n)} to {pct(os_on, os_n)} PASS, the agents from {pct(ag_off, ag_n)} to {pct(ag_on, ag_n)}.</span></div>
    <div class="takeaway"><b>Thinking made the agents read half as much</b><span>L7's median input per run fell from {l7_in[0]:.0f}k to {l7_in[1]:.0f}k tokens, and the thinking-on round used fewer tokens in total ({(in_off + out_off) / 1e6:.0f}M → {(in_on + out_on) / 1e6:.0f}M) despite the extra output.</span></div>
  </section>

  <section>
    <div class="eyebrow">Hypotheses</div>
    <h2>What was expected and what was found</h2>
    <p class="prose">Each verdict holds for this model, these 30 tasks and this codebase, with one sample per
    configuration. Read them as observations from this experiment, to test elsewhere before relying on them. The sections after this table give the evidence behind each verdict.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Expectation</th><th>Verdict</th><th>Evidence</th></tr></thead>
      <tbody>{hyp_rows}</tbody></table></div>
  </section>

  <section>
    <div class="eyebrow">Results</div>
    <h2>Pass rate climbed with context, and the agents came out on top</h2>
    <p class="prose">Each row pools every task and repo for one level. The hollow dot marks thinking off, the filled dot
    thinking on, and PASS means the answer cleared all of the task's hidden tests.</p>
    <div class="chart-legend"><span><i style="border:2px solid var(--accent)"></i>thinking off</span><span><i style="background:var(--accent)"></i>thinking on</span><span><i style="background:var(--muted)"></i>L-AG, an outside reference</span></div>
    {dumbbell(ladder)}
    <p class="small">L1 to L6 open with the file the change belongs in. L2 adds that file's imports and its test, L3 the
    whole repository, and L4 to L6 whatever search or the signature map turns up. The agents get the file tree instead
    and choose what to read. L0 had 2 control runs, too few for a row. The Levels page covers every variant.</p>
  </section>

  <section>
    <div class="eyebrow">Repository size</div>
    <h2>Where each approach held up</h2>
    <p class="prose">The same pass rates, thinking on, split by repository. S holds about 2k tokens and M about 41k. L
    reaches 169k and reads like a year of growth, with old code that still carries old names. Darker cells pass more.</p>
    <div class="heat">{heat_rows}</div>
    <p class="small">L1 and L2 run each task once, on its smallest repo, so their columns hold different tasks. L3 can't
    run on repo L at all, since that repository outgrows the model's 98k-token window. On these three repositories,
    search-based levels slipped as the repository grew, and the agents stayed above 80% on every size.</p>
  </section>

  <section>
    <div class="eyebrow">Reuse</div>
    <h2>The model reused what it could see</h2>
    <p class="prose">On reuse tasks a hidden spy watches whether the new code calls the helper the task needs, and each
    bar groups those runs by how much of the helper sat in the model's context when it answered.</p>
    <div class="chart-legend"><span><i style="background:var(--accent);border-radius:2px"></i>thinking on</span><span><i style="background:color-mix(in srgb, var(--accent) 45%, var(--surface));border-radius:2px"></i>thinking off</span></div>
    {hbars(reuse_rows, 100)}
    <p class="small">With the full source in view, the model reused the helper in {pct(vf[1], vf[0])} of runs. With thinking, a
    bare signature worked almost as well ({pct(v_on['its signature only'][1], v_on['its signature only'][0])}), and without
    thinking it fell to {pct(v_off['its signature only'][1], v_off['its signature only'][0])}. With nothing of the helper in view,
    the model wrote its own, except in {vn[1] + v_off['nothing of it'][1]} runs that guessed a conventional name from a
    visible module, the task's wording or the reuse hint (<a href="cases">Case studies</a>). The agents nearly always
    found the helper, so their reuse came close to 100%.</p>
  </section>

  <section>
    <div class="eyebrow">Tokens</div>
    <h2>What each level cost</h2>
    <div>
      <div>
        <h3>All runs, per round</h3>
        <div class="chart-legend"><span><i style="background:var(--accent);border-radius:2px"></i>input</span><span><i style="background:var(--warn);border-radius:2px"></i>output (thinking included)</span></div>
        {round_bars}
        <p class="small">In this round thinking cost about {out_on / out_off:.0f}× more output, and with it the agents read
        less: L7's median input per run dropped from {l7_in[0]:.0f}k to {l7_in[1]:.0f}k. Added up, the thinking-on round
        spent fewer tokens.</p>
      </div>
      <div>
        <h3>Tokens per PASS, thinking on</h3>
        {tpp_bars}
        <p class="small">Every token a level spent, divided by its passing runs. Search-based levels passed cheapest, at
        {ret_tpp / 1e3:.0f}k. L3 needed {l3_tpp / 1e3:.0f}k on the repos it fits. The agents paid more, {l7_tpp / 1e3:.0f}k for L7
        and {l8_tpp / 1e3:.0f}k for L8, and passed above 90% on every repo size for it. L1 spent
        {tpp[0][1] / 1e3:.0f}k per PASS, most of it thinking about code the model couldn't see. L-AG spent {lag_tpp / 1e3:.0f}k.</p>
      </div>
    </div>
    <div class="table-wrap"><table>
      <thead><tr><th>Level</th><th class=r>Input, off</th><th class=r>Input, on</th><th class=r>Output, off</th><th class=r>Output, on</th><th class=r>Largest prompt, on</th></tr></thead>
      <tbody>{''.join(tok_rows)}</tbody></table></div>
    <p class="small">Medians per run, counting only runs that reached the model (L3 never did on repo L). The largest
    prompt is the biggest single request in a run, the number that has to fit the window. An agent's input piles up over
    many requests, each repeating most of the one before, so a provider with prompt caching would probably bill a good part
    of it at a discount. At {PRICES['open-weights host'][0]:.2f} and {PRICES['open-weights host'][1]:.2f} USD per million input and output tokens, a typical
    open-weights price, the whole thinking-on round comes to about {usd(in_on, out_on, 'open-weights host'):.0f} USD. Frontier-API
    prices of {PRICES['frontier API'][0]:.0f} and {PRICES['frontier API'][1]:.0f} USD would make it about {usd(in_on, out_on, 'frontier API'):.0f} USD.</p>
  </section>

  <section>
    <div class="eyebrow">Quality</div>
    <h2>How clean the passing code is</h2>
    <p class="prose">A blind judge, Gemini 4 Neon in Google Antigravity (agy), read every passing patch. It never learned which level produced a patch, and it
    flagged what tests can't see: unrelated edits, re-implemented helpers, broken conventions, logic that looks wrong where
    no test reaches, and risky code. A clean pass clears the hidden tests, leaves the repository's own tests green and draws no
    flag from the judge.</p>
    <div class="chart-legend"><span><i style="background:var(--good);border-radius:2px"></i>clean pass</span><span><i style="background:color-mix(in srgb, var(--good) 35%, var(--surface));border-radius:2px"></i>pass with a judged defect</span></div>
    {hbars(q_rows, 100)}
    <p class="small">Share of all thinking-on runs. The agents led here as well, with L8 at {clean_rate['L8']:.0f}% clean.
    The collapsed penalty tables at the bottom list the judge's codes per configuration.</p>
  </section>

  <section>
    <div class="eyebrow">Reference</div>
    <h2>A commercial agent harness as the baseline</h2>
    <p class="prose">L-AG served as a baseline of what a complete agent harness achieves on these tasks. It runs Google's
    Antigravity, a commercial product with many thousands of hours of development behind it: its own system prompt, its
    own tools, shell commands included, and its own loop. The primitive levels on this page are harnesses put together in
    a few days for this experiment.</p>
    <p class="prose">For the baseline, Antigravity was paired with Gemini 3.5 Flash-Lite, one of the cheapest Gemini models.
    These 30 tasks are a simple test suite, well below the level at which frontier models are benchmarked today, so a
    small, inexpensive model was enough to show what the harness itself contributes. The same harness also ran with Qwen
    3.8 27B, the model under study everywhere else.</p>
    <div class="table-wrap"><table>
      <thead><tr><th>Harness + model</th><th class=r>PASS</th><th class=r>Shell commands that ran nothing</th><th class=r>Median input per run</th></tr></thead>
      <tbody>{''.join(base_rows)}</tbody></table></div>
    <p class="prose">The pairing of Antigravity and Gemini showed a high synergy. None of Gemini's {gem_n} shell commands ran empty, and
    the pair passed {passed(gem)} of {len(gem)}, though it didn't reach every task: it missed {', '.join(gem_miss)}. Qwen in
    the same harness often named tool arguments in its own style, which Antigravity ignores, so many of its commands ran
    nothing (the Levels page has the details).</p>
    <p class="prose">The primitive harnesses paired with a mid-size local model held their own. On these tasks, L8 with Qwen
    passed {passed(by_cfg.get('L8', []))} of 57 and L7 selective {passed(by_cfg.get('L7 selective', []))}, on less input per
    run than either Antigravity pairing.</p>
    <p class="small">Wall-clock times can't be compared: Gemini ran on Google's hosted API, six runs at a time, while Qwen
    ran on two local GPUs, two at a time. Every pairing ran one sample per task.</p>
  </section>

  <section>
    <div class="eyebrow">Side experiment</div>
    <h2>Qwen's own tool names</h2>
    <p class="prose">A side arm, thinking off only, gave the agents' tools the names and parameters of Qwen Code, Qwen's
    own coding agent (<code>read_file(file_path, offset, limit)</code>, <code>grep_search</code>,
    <code>edit(file_path, old_string, new_string)</code>, <code>write_file</code>). The capabilities underneath stayed the
    same. Would this model work better in the tool vocabulary it learned?</p>
    <div class="chart-legend"><span><i style="background:color-mix(in srgb, var(--accent) 45%, var(--surface));border-radius:2px"></i>harness names</span><span><i style="background:var(--accent);border-radius:2px"></i>Qwen's names</span></div>
    {side_bars}
    <div class="table-wrap"><table>
      <thead><tr><th>Agent</th><th>Tool names</th><th class=r>PASS</th><th class=r>Clean passes</th><th class=r>Failed tool calls</th>
      <th class=r>Runs with a failed call</th><th class=r>Tool calls</th><th class=r>Input</th></tr></thead>
      <tbody>{side_rows}</tbody></table></div>
    <ul class="prose">
      <li><b>L8 ran smoother with Qwen's names.</b> Failed tool calls fell from {s8['errs']} to {q8['errs']}, and runs with a
      failed call from {s8['err_runs']} to {q8['err_runs']}. The runs took fewer calls ({s8['calls']:.0f} → {q8['calls']:.0f}) and
      less input ({s8['tin']:.0f}k → {q8['tin']:.0f}k), and the pass rate moved from {s8['p']} to {q8['p']} of 57.</li>
      <li><b>L7 selective didn't change</b> ({s7['p']} → {q7['p']} of 57). Only some of its tools have a Qwen Code
      counterpart, and its failed calls rose ({s7['errs']} → {q7['errs']}), which may come from that mix of the two vocabularies.</li>
      <li>The pass-rate differences sit within the noise of one sample. Here the names showed up in how smoothly L8 worked,
      which fits the far stronger tool-call drift Qwen showed inside Antigravity.</li>
    </ul>
    <p class="small">Tool calls and input: medians per run. Clean pass: passes the tests, keeps the repository's own tests
    green, and the judge found no defect.</p>
  </section>

  <section>
    <div class="eyebrow">Limits</div>
    <h2>How certain the results are</h2>
    <ul class="prose">
      <li>One model and one sample per configuration. Repeating a run flips about 1 outcome in 5, roughly ±3 per 30 runs,
      so a gap of a few runs between two configurations means little. The gaps between context levels on this page are many
      times that size.</li>
      <li>All tasks run on one synthetic shop service, in three sizes, written for this experiment so the model can't have
      seen it before. Real repositories are messier.</li>
      <li>The judge is a model too. Its findings rate how clean a passing patch is, and PASS or FAIL comes from the tests alone.</li>
    </ul>
  </section>

  <section>
    <div class="eyebrow">Details</div>
    <h2>Every configuration</h2>
    <details class="more"><summary>Thinking on: all configurations</summary>{cfg_table(list(final.values()))}</details>
    <details class="more"><summary>Thinking off: all configurations</summary>{cfg_table(list(off.values()))}</details>
    <details class="more"><summary>Judged code quality: penalties per configuration</summary>
      {penalty_section(main(off.values()), "thinking off")}{penalty_section(main(final.values()), "thinking on", "penalties-on")}</details>
    <p class="small">S, M, L: passing runs out of runs on each repo. Reuse: reuse tasks where the spy saw the helper called.
    Input and output: medians per run.</p>
  </section>

  <section>
    <div class="next-pages">
      <a href="levels"><b>Levels</b><span>Every level and variant, how the agents search and read, and the L-AG harness.</span></a>
      <a href="method"><b>Method</b><span>The rules that keep the comparison fair, the outcomes and the grading.</span></a>
      <a href="tasks"><b>Tasks</b><span>Every task with its text, what it tests, its hidden tests and reference solution.</span></a>
      <a href="cases"><b>Case studies</b><span>Single runs worth reading in full: guessed helpers, an agent caught by its own test, the trap, empty commands.</span></a>
      <a href="data"><b>Raw data</b><span>Every run with its prompt, thinking, answer, patch and test output, side by side if you like.</span></a>
    </div>
  </section>
""")
    return page


INDEX_TITLE = "Context Bench: How Context Building Affects Generated Code"
INDEX_DESCRIPTION = ("The same coding tasks and the same model (Qwen 3.8 27B), with the context built in deliberately "
                     "simple ways, from one file and the whole repo to retrieval, a repo map and minimal agents with "
                     "tools, on three repository sizes, with thinking off and on, and what each costs in tokens.")


def _page(content: str) -> str:
    """The Results page: generated content in the site's one layout (site_layout.py)."""
    return site_layout.render(content, page="index", title=INDEX_TITLE, description=INDEX_DESCRIPTION)

if __name__ == "__main__":
    site = Path(next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--out=")), ROOT / "docs"))
    site = site if site.is_absolute() else ROOT / site
    (site / "index.html").write_text(build())
    print(f"wrote {site / 'index.html'}")
