// Raw-data viewer: list every run, open one or two side by side, prompt split into coloured parts.
// State lives in the URL hash (#a=<run>&b=<run>&tab=prompt&...), so any view can be linked.
(() => {
  "use strict";
  const KINDS = {
    system: "Agent identity and rules", hint: "Reuse hint", target: "Target file", file: "Repo files",
    retrieved: "Retrieved code", map: "Repository map", tools: "Tool instructions / calls", task: "Task",
    closing: "Closing lines",
  };
  const TABS = [["prompt", "Prompt"], ["thinking", "Thinking"], ["answer", "Answer / transcript"], ["patch", "Patch"], ["tests", "Tests"]];
  const OPEN_BY_DEFAULT = new Set(["task", "hint", "tools", "target", "closing"]);
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (n) => (n == null ? "–" : Number(n).toLocaleString("en-US"));
  const tok = (chars) => Math.round(chars / 3.6);
  const cache = new Map();
  let runs = [], byId = new Map(), sortKey = "task_id", sortDir = 1;

  // ---------- state ----------
  const state = { a: null, b: null, tab: "prompt", round: "final", task: "", level: "", label: "", q: "" };
  function readHash() {
    const h = new URLSearchParams(location.hash.slice(1));
    for (const k of Object.keys(state)) if (h.has(k)) state[k] = h.get(k);
  }
  function writeHash() {
    const h = new URLSearchParams();
    for (const [k, v] of Object.entries(state)) if (v) h.set(k, v);
    history.replaceState(null, "", "#" + h.toString());
  }

  // ---------- run list ----------
  // report outcomes (stored labels stay ok/broken/...): PASS, FAIL, INVALID, OUT OF TOKENS, NO FIT
  const badge = (o) => `<span class="badge ${o === "PASS" ? "b-ok" : o === "FAIL" ? "b-broken" : "b-other"}">${esc(o)}</span>`;
  const yn = (v) => (v == null ? "–" : v ? "yes" : "no");
  const frac = (a, b) => (b ? `${a}/${b}` : "–");
  const COLS = [
    ["round", "Round", (r) => (r.round === "final" ? "final" : "think off")],
    ["task_id", "Task", (r) => esc(r.task_id)],
    ["level_label", "Level", (r) => esc(r.level_label)],
    ["repo_size", "Repo", (r) => r.repo_size],
    ["outcome", "Outcome", (r) => badge(r.outcome)],
    ["tests_passed", "Tests", (r) => frac(r.tests_passed, r.tests_total)],
    ["reuse_passed", "Reuse", (r) => yn(r.reuse_passed)],
    ["repo_tests_passed", "Repo tests", (r) => frac(r.repo_tests_passed, r.repo_tests_total)],
    ["tokens_in", "In", (r) => fmt(r.tokens_in)],
    ["tokens_out", "Out", (r) => fmt(r.tokens_out)],
    ["latency_s", "Sec", (r) => (r.latency_s == null ? "–" : Math.round(r.latency_s))],
    ["tool_calls", "Tools", (r) => r.tool_calls ?? "–"],
  ];

  function filtered() {
    const q = state.q.toLowerCase();
    return runs.filter((r) =>
      (!state.round || state.round === "all" || r.round === state.round) &&
      (!state.task || r.task_id === state.task) &&
      (!state.level || r.level_label === state.level) &&
      (!state.label || r.outcome === state.label) &&
      (!q || `${r.task_id} ${r.level_label} ${r.category} ${r.run_id}`.toLowerCase().includes(q)));
  }

  function renderTable() {
    const rows = filtered().sort((x, y) => {
      const a = x[sortKey], b = y[sortKey];
      return (a == null) - (b == null) || (a > b ? 1 : a < b ? -1 : 0) * sortDir;
    });
    $("#count").textContent = `${rows.length} of ${runs.length} runs`;
    const head = COLS.map(([k, t]) => `<th data-k="${k}" ${k === sortKey ? `aria-sort="${sortDir > 0 ? "ascending" : "descending"}"` : ""} class="${["tokens_in", "tokens_out", "latency_s", "tool_calls"].includes(k) ? "r" : ""}">${t}</th>`).join("");
    const body = rows.map((r) => `<tr data-id="${r.run_id}" class="${r.run_id === state.a ? "sel-a" : r.run_id === state.b ? "sel-b" : ""}">` +
      COLS.map(([k, , f]) => `<td class="${["tokens_in", "tokens_out", "latency_s", "tool_calls"].includes(k) ? "r" : ""}">${f(r)}</td>`).join("") +
      `<td><button class="pick" data-b="${r.run_id}" title="Open in the right pane to compare">B</button></td></tr>`).join("");
    $("#runs").innerHTML = `<table><thead><tr>${head}<th></th></tr></thead><tbody>${body}</tbody></table>`;
  }

  function fillFilters() {
    const opts = (vals, cur, all) => `<option value="">${all}</option>` + vals.map((v) => `<option ${v === cur ? "selected" : ""}>${esc(v)}</option>`).join("");
    const uniq = (k) => [...new Set(runs.map((r) => r[k]))].sort();
    $("#f-task").innerHTML = opts(uniq("task_id"), state.task, "All tasks");
    $("#f-level").innerHTML = opts(uniq("level_label"), state.level, "All levels");
    $("#f-label").innerHTML = opts(uniq("outcome"), state.label, "Any outcome");
    $("#f-round").value = state.round;
    $("#f-q").value = state.q;
  }

  // ---------- one run ----------
  async function load(id) {
    if (!cache.has(id)) cache.set(id, fetch(`raw/runs/${id}.json`).then((r) => r.json()));
    return cache.get(id);
  }

  function compBar(run) {
    const segs = [...run.system, ...run.user];
    const total = segs.reduce((s, x) => s + x.text.length, 0) || 1;
    const parts = segs.map((s) => `<div class="k-${s.kind}" style="flex:${s.text.length / total}" title="${esc(s.label)}: ≈${fmt(tok(s.text.length))} tok"></div>`).join("");
    return `<div class="comp-wrap"><div class="comp" role="img" aria-label="Prompt composition">${parts}</div>
      <div class="meta"><span>initial prompt ≈${fmt(tok(total))} tok (est. from characters)</span><span>${segs.length} parts</span></div></div>`;
  }

  function segHTML(s) {
    const open = OPEN_BY_DEFAULT.has(s.kind) && s.text.length < 6000 ? " open" : "";
    return `<details class="seg k-${s.kind}"${open}><summary><span class="lab">${esc(s.label)}</span><span class="sz">≈${fmt(tok(s.text.length))} tok</span></summary><pre>${esc(s.text)}</pre></details>`;
  }

  function promptHTML(run) {
    return `${compBar(run)}<div class="segs"><div class="group-label">System message</div>${run.system.map(segHTML).join("")}
      <div class="group-label">User message</div>${run.user.map(segHTML).join("")}</div>`;
  }

  function diffHTML(text) {
    if (!text) return `<p class="empty">No patch (no valid file blocks).</p>`;
    const lines = esc(text).split("\n").map((l) =>
      l.startsWith("@@") ? `<span class="hunk">${l}</span>` : l.startsWith("+") ? `<span class="add">${l}</span>` : l.startsWith("-") ? `<span class="del">${l}</span>` : l);
    return `<pre class="block diff">${lines.join("\n")}</pre>`;
  }

  function transcriptHTML(run) {
    const t = run.transcript || [];
    if (!t.length) return run.response ? `<pre class="block">${esc(run.response)}</pre>` : `<p class="empty">No answer saved.</p>`;
    const names = {}, droppedIds = new Set();
    t.forEach((m) => (m.tool_calls || []).forEach((c) => (names[c.id] = c.name)));
    t.forEach((m) => (m.drop || []).forEach((d) => droppedIds.add(d.id)));
    return `<div class="segs">` + t.map((m) => {
      if (m.role === "step") {
        return `<div class="turn"><div class="who">Planning call (${esc(m.step)})</div>
          <details class="seg k-tools"><summary><span class="lab">Instruction</span></summary><pre>${esc(m.instruction)}</pre></details>
          <details class="seg k-tools" open><summary><span class="lab">Model's queries</span></summary><pre>${esc(m.reply)}</pre></details>
          <details class="seg k-retrieved"><summary><span class="lab">Hits per query</span></summary><pre>${esc(m.hits)}</pre></details></div>`;
      }
      if (m.role === "tool" && m.drop) {
        return `<div class="call"><b>dropped</b> ${m.drop.map((d) => esc(d.stub)).join(" · ")}</div>`;
      }
      if (m.role === "tool" && droppedIds.has(m.result_id)) {
        return `<details class="seg k-closing"><summary><span class="lab">#${m.result_id} · ${esc(names[m.tool_call_id] || "tool")} · dropped later (stub sent from then on)</span><span class="sz">≈${fmt(tok((m.content || "").length))} tok</span></summary><pre>${esc(m.content)}</pre></details>`;
      }
      if (m.role === "tool") {
        return `<details class="seg k-tools"><summary><span class="lab">Result · ${esc(names[m.tool_call_id] || "tool")}</span><span class="sz">≈${fmt(tok((m.content || "").length))} tok</span></summary><pre>${esc(m.content)}</pre></details>`;
      }
      if (m.role === "user") {
        return `<details class="seg k-closing" open><summary><span class="lab">Harness message</span></summary><pre>${esc(m.content)}</pre></details>`;
      }
      const calls = (m.tool_calls || []).map((c) => `<div class="call"><b>${esc(c.name)}</b> ${esc(c.arguments)}</div>`).join("");
      const body = (m.content || "").trim() ? `<pre class="block">${esc(m.content.trim())}</pre>` : "";
      return `<div class="turn"><div class="who">Model${m.finish_reason ? ` · ${esc(m.finish_reason)}` : ""}</div>${body}${calls}</div>`;
    }).join("") + `</div>`;
  }

  function paneHTML(run, side) {
    const m = run.meta;
    const facts = [
      ["round", m.round === "final" ? "final (thinking, cold)" : "thinking off"],
      ["tests", frac(m.tests_passed, m.tests_total)], ["reuse", yn(m.reuse_passed)],
      ["repo tests", frac(m.repo_tests_passed, m.repo_tests_total)], ["format", m.format_status],
      ["in", fmt(m.tokens_in)], ["cached", fmt(m.tokens_cached)], ["out", fmt(m.tokens_out)],
      ["sec", m.latency_s == null ? "–" : Math.round(m.latency_s)], ["tools", m.tool_calls ?? 0],
      ["helper seen", m.helper_body_in_context ? "body" : m.helper_in_context ? "signature" : "no"],
      ...(m.drops == null ? [] : [["dropped", `${m.drops} (${m.drop_calls} calls)`],
        ["helper body at answer", yn(m.helper_body_at_answer)]]),
    ].map(([k, v]) => `<span>${k} <b>${esc(v)}</b></span>`).join("");
    let body;
    switch (state.tab) {
      case "prompt": body = promptHTML(run); break;
      case "thinking": body = run.reasoning ? `<pre class="block">${esc(run.reasoning)}</pre>` : `<p class="empty">No thinking (thinking off, or none returned).</p>`; break;
      case "answer": body = transcriptHTML(run); break;
      case "patch": body = diffHTML(run.patch); break;
      case "tests": body = run.tests ? `<pre class="block">${esc(run.tests)}</pre>` : `<p class="empty">No test output.</p>`; break;
    }
    const close = side === "b" ? `<button class="pick" data-close="b">close</button>` : "";
    return `<div class="pane ${side}"><div class="pane-head"><h3>${side.toUpperCase()} · ${esc(m.task_id)} · ${esc(m.level_label)} · ${esc(m.repo_size)} ${badge(m.outcome)} ${close}</h3>
      <div class="facts">${facts}</div><div class="meta">run <a href="#a=${m.run_id}">${m.run_id}</a> · target ${esc(m.target_file)}</div></div>${body}</div>`;
  }

  function twinOptions(run) {
    // other runs of the same task: the quickest way to pick a comparison
    const m = run.meta;
    const twins = runs.filter((r) => r.task_id === m.task_id && r.run_id !== m.run_id)
      .sort((x, y) => x.round.localeCompare(y.round) || x.level_label.localeCompare(y.level_label) || x.repo_size.localeCompare(y.repo_size));
    return `<label>Compare A with <select id="twin"><option value="">same task, another run…</option>` +
      twins.map((r) => `<option value="${r.run_id}" ${r.run_id === state.b ? "selected" : ""}>${esc(r.level_label)} · ${r.repo_size} · ${r.round === "final" ? "final" : "think off"} · ${r.outcome}</option>`).join("") +
      `</select></label>`;
  }

  async function renderPanes() {
    const el = $("#panes");
    $("#tabs").innerHTML = TABS.map(([k, t]) => `<button role="tab" data-tab="${k}" aria-selected="${state.tab === k}">${t}</button>`).join("");
    if (!state.a) {
      el.className = "panes";
      el.innerHTML = `<p class="empty">Pick a run in the table to open it. Use <b>B</b> on another row, or the menu that appears, to compare two runs side by side.</p>`;
      $("#twin-wrap").innerHTML = "";
      return;
    }
    const [a, b] = await Promise.all([load(state.a), state.b ? load(state.b) : null]);
    $("#twin-wrap").innerHTML = twinOptions(a);
    el.className = b ? "panes two" : "panes";
    el.innerHTML = paneHTML(a, "a") + (b ? paneHTML(b, "b") : "");
  }

  function render() { writeHash(); renderTable(); renderPanes(); }

  // ---------- events ----------
  function wire() {
    const on = (id, ev, fn) => $(id).addEventListener(ev, fn);
    on("#f-round", "change", (e) => { state.round = e.target.value; render(); });
    on("#f-task", "change", (e) => { state.task = e.target.value; render(); });
    on("#f-level", "change", (e) => { state.level = e.target.value; render(); });
    on("#f-label", "change", (e) => { state.label = e.target.value; render(); });
    on("#f-q", "input", (e) => { state.q = e.target.value; render(); });
    on("#runs", "click", (e) => {
      const th = e.target.closest("th[data-k]");
      if (th) { sortDir = sortKey === th.dataset.k ? -sortDir : 1; sortKey = th.dataset.k; return renderTable(); }
      const pb = e.target.closest("[data-b]");
      if (pb) { state.b = pb.dataset.b === state.a ? null : pb.dataset.b; return render(); }
      const tr = e.target.closest("tr[data-id]");
      if (tr) { state.a = tr.dataset.id; if (state.b === state.a) state.b = null; render(); }
    });
    on("#tabs", "click", (e) => { const b = e.target.closest("[data-tab]"); if (b) { state.tab = b.dataset.tab; render(); } });
    on("#twin-wrap", "change", (e) => { if (e.target.id === "twin") { state.b = e.target.value || null; render(); } });
    on("#panes", "click", (e) => { if (e.target.closest("[data-close]")) { state.b = null; render(); } });
    on("#expand", "click", () => document.querySelectorAll("#panes details.seg").forEach((d) => (d.open = true)));
    on("#collapse", "click", () => document.querySelectorAll("#panes details.seg").forEach((d) => (d.open = false)));
    window.addEventListener("hashchange", () => { readHash(); fillFilters(); renderTable(); renderPanes(); });
  }

  async function init() {
    readHash();
    $("#legend").innerHTML = Object.entries(KINDS).map(([k, t]) => `<span class="k-${k}"><i></i>${t}</span>`).join("");
    runs = await fetch("raw/index.json").then((r) => r.json());
    byId = new Map(runs.map((r) => [r.run_id, r]));
    if (state.a && !byId.has(state.a)) state.a = null;
    if (state.b && !byId.has(state.b)) state.b = null;
    fillFilters();
    wire();
    render();
  }
  init().catch((e) => { $("#panes").innerHTML = `<p class="empty">Couldn't load the data: ${esc(e.message)}</p>`; });
})();
