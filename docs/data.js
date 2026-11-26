// Raw-data viewer: list every run, open one or two side by side, prompt split into coloured parts.
// State lives in the URL hash (#a=<run>&b=<run>&tab=prompt&...), so any view can be linked.
(() => {
  "use strict";
  const KINDS = {
    system: "Agent identity and rules", hint: "Reuse hint", target: "Target file", file: "Repo files",
    retrieved: "Retrieved code", map: "Repository map", tools: "Tool instructions / calls", task: "Task",
    closing: "Closing lines",
  };
  const TABS = [["prompt", "Prompt"], ["thinking", "Thinking"], ["answer", "Answer / transcript"], ["patch", "Diff"], ["tests", "Tests"]];
  const OPEN_BY_DEFAULT = new Set(["task", "hint", "tools", "target", "closing"]);
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (n) => (n == null ? "–" : Number(n).toLocaleString("en-US"));
  const tok = (chars) => Math.round(chars / 3.6);
  const cache = new Map();
  let runs = [], byId = new Map(), tasks = new Map(), sortKey = "task_id", sortDir = 1;

  // ---------- state ----------
  const state = { view: "table", a: null, b: null, tab: "prompt", round: "final", task: "", level: "", label: "", q: "" };
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
  // report outcomes (stored labels stay ok/broken/...): PASS, FAIL, UNPARSABLE, ASKED TO EXPLORE, NO ANSWER, INVALID,
  // OUT OF TOKENS, NO FIT
  const badge = (o) => `<span class="badge ${o === "PASS" ? "b-ok" : o === "FAIL" ? "b-broken" : "b-other"}">${esc(o)}</span>`;
  const yn = (v) => (v == null ? "–" : v ? "yes" : "no");
  const frac = (a, b) => (b ? `${a}/${b}` : "–");
  const COLS = [
    ["round", "Round", (r) => (r.round === "final" ? "think on" : "think off")],
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
    document.querySelectorAll("#views [data-view]").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.view === state.view)));
    $("#runs").classList.toggle("mx-mode", state.view === "matrix");
    $("#mx-legend").hidden = state.view !== "matrix";
    if (state.view === "matrix") { renderMatrixLegend(); return renderMatrix(); }
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


  // ---------- matrix view: tasks (rows) x harness levels (columns); each cell stacks the repos S, M, L ----------
  // Outcome colours are the Results page's (.o-*); the tooltip and the table view carry the same facts as text.
  const OCLS = {"PASS": "o-pass", "FAIL": "o-fail", "UNPARSABLE": "o-unparsable", "ASKED TO EXPLORE": "o-explore",
    "NO ANSWER": "o-noanswer", "INVALID": "o-invalid", "OUT OF TOKENS": "o-oot", "NO FIT": "o-nofit"};
  const REPOS = ["S", "M", "L"];
  const CATS = ["control", "reuse", "convention", "cross_file", "trap", "long"];
  // compact column labels (the full label is the column's tooltip; the legend explains the abbreviations)
  const shortVariant = (label, level) => {
    const v = label.slice(level.length).trim()
      .replace("task_vs_code", "").replace("model_queries", "q").replace("embed", "emb").replace("+hint", "+h")
      .replace("selective", "sel").replace("hybrid", "hyb").replace("qwen names", "qw").replace(/\s+/g, " ").trim();
    return v || "·";
  };
  // L-AG (an external harness, not one of the context levels) always comes last
  const lagLast = (x) => (String(x).startsWith("L-AG") ? 1 : 0);
  const byLabel = (x, y) => lagLast(x) - lagLast(y) || String(x).localeCompare(String(y), "en", {numeric: true});
  const levelOrder = (a, b) => lagLast(a.level) - lagLast(b.level) || a.level.localeCompare(b.level, "en", {numeric: true}) || a.label.localeCompare(b.label);

  function renderMatrix() {
    const scope = runs.filter((r) =>
      (!state.round || state.round === "all" || r.round === state.round) &&
      (!state.task || r.task_id === state.task) && (!state.level || r.level_label === state.level));
    const match = new Set(filtered().map((r) => r.run_id));   // outcome and search filters dim the rest
    const rounds = state.round === "all" ? ["final", "nothink"] : [state.round || "final"];
    const cols = [...new Map(scope.map((r) => [r.level_label, {label: r.level_label, level: r.level}])).values()].sort(levelOrder);
    const cell = new Map(scope.map((r) => [`${r.task_id}|${r.level_label}|${r.repo_size}|${r.round}`, r]));
    // thinking on alone: PASS totals also show the change against thinking off, over the runs both rounds have
    const showDelta = rounds.length === 1 && rounds[0] === "final";
    const offRun = new Map(showDelta ? runs.filter((r) => r.round === "nothink").map((r) => [`${r.task_id}|${r.level_label}|${r.repo_size}`, r]) : []);
    const delta = (d, pairs, pp) => {
      if (!showDelta || !pairs) return "";
      const v = pp ? Math.round(100 * d / pairs) : d;
      const txt = v > 0 ? `+${v}` : v < 0 ? `−${-v}` : "±0";
      return `<span class="mx-d ${v > 0 ? "up" : v < 0 ? "down" : ""}" title="change against thinking off over the ${pairs} runs both rounds have${pp ? " (percentage points)" : ""}">${txt}</span>`;
    };
    const taskIds = [...new Set(scope.map((r) => r.task_id))];
    const meta = (id) => tasks.get(id) || {category: byTaskCat(id), tier: 0, repo_sizes: REPOS};
    taskIds.sort((a, b) => CATS.indexOf(meta(a).category) - CATS.indexOf(meta(b).category) || meta(a).tier - meta(b).tier || a.localeCompare(b, "en", {numeric: true}));
    $("#count").textContent = `${match.size} of ${runs.length} runs`;

    // header: level groups, then variants (vertical)
    const groups = [];
    for (const c of cols) (groups.at(-1)?.level === c.level ? groups.at(-1).n++ : groups.push({level: c.level, n: 1}));
    const head1 = `<tr><th class="mx-corner" rowspan="2">Task</th>`
      + groups.map((g) => `<th class="mx-lv" colspan="${g.n}">${esc(g.level)}</th>`).join("") + `<th class="mx-sum" rowspan="2">PASS</th></tr>`;
    const head2 = `<tr>` + cols.map((c) => `<th class="mx-var" title="${esc(c.label)}"><span>${esc(shortVariant(c.label, c.level))}</span></th>`).join("") + `</tr>`;

    const colPass = cols.map(() => [0, 0]), colDelta = cols.map(() => [0, 0]);
    let body = "", lastCat = null;
    for (const id of taskIds) {
      const m = meta(id);
      if (m.category !== lastCat) {
        lastCat = m.category;
        body += `<tr class="mx-cat"><th colspan="${cols.length + 2}">${esc(m.category.replace("_", "-"))}</th></tr>`;
      }
      let pass = 0, n = 0, rowD = 0, rowPairs = 0;
      const tds = cols.map((c, ci) => {
        const stacks = rounds.map((rd) => `<span class="mx-stack">` + REPOS.map((rs) => {
          const r = cell.get(`${id}|${c.label}|${rs}|${rd}`);
          if (!r) return `<span class="mx-chip ${m.repo_sizes.includes(rs) ? "mx-none" : "mx-na"}" aria-hidden="true"></span>`;
          n++; colPass[ci][1]++;
          if (r.outcome === "PASS") { pass++; colPass[ci][0]++; }
          const off = showDelta && offRun.get(`${id}|${c.label}|${rs}`);
          if (off) { const d = (r.outcome === "PASS") - (off.outcome === "PASS"); rowD += d; rowPairs++; colDelta[ci][0] += d; colDelta[ci][1]++; }
          const sel = r.run_id === state.a ? " sel-a" : r.run_id === state.b ? " sel-b" : "";
          const tip = `${r.task_id} · ${r.level_label} · repo ${r.repo_size} · ${r.round === "final" ? "think on" : "think off"}\n${r.outcome}`
            + ` · tests ${frac(r.tests_passed, r.tests_total)}${r.reuse_passed == null ? "" : ` · reuse ${yn(r.reuse_passed)}`}`
            + `\nin ${fmt(r.tokens_in)} · out ${fmt(r.tokens_out)} · ${r.latency_s == null ? "–" : Math.round(r.latency_s)} s${r.tool_calls ? ` · ${r.tool_calls} tools` : ""}`
            + `\nopened by click · compared (B) by shift-click`;
          return `<button class="mx-chip ${OCLS[r.outcome] || "o-invalid"}${sel}${match.has(r.run_id) ? "" : " mx-dim"}" data-run="${r.run_id}" title="${esc(tip)}" aria-label="${esc(tip.split("\n").slice(0, 2).join(", "))}"></button>`;
        }).join("") + `</span>`).join("");
        return `<td class="mx-cell">${stacks}</td>`;
      }).join("");
      body += `<tr><th class="mx-task" title="tier ${m.tier}">${esc(id)} <span class="mx-rl">${REPOS.map((rs) => `<i class="${m.repo_sizes.includes(rs) ? "" : "off"}">${rs}</i>`).join("")}</span></th>${tds}<td class="mx-sum">${n ? `${pass}/${n}` : "–"}${delta(rowD, rowPairs)}</td></tr>`;
    }
    const foot = `<tr class="mx-foot"><th>PASS</th>` + colPass.map(([p, n], ci) => `<td class="mx-sum" title="${p} of ${n} runs">${n ? `${Math.round(100 * p / n)}%` : "–"}${delta(colDelta[ci][0], colDelta[ci][1], true)}</td>`).join("") + `<td></td></tr>`;
    $("#runs").innerHTML = taskIds.length
      ? `<table class="mx"><thead>${head1}${head2}</thead><tbody>${body}</tbody><tfoot>${foot}</tfoot></table>`
      : `<p class="empty">No runs match the filters.</p>`;
  }
  const byTaskCat = (id) => (runs.find((r) => r.task_id === id) || {}).category || "control";

  function renderMatrixLegend() {
    $("#mx-legend").innerHTML = Object.entries(OCLS).map(([o, c]) => `<span><i class="mx-chip ${c}"></i>${esc(o)}</span>`).join("")
      + `<span><i class="mx-chip mx-none"></i>no run</span><span><i class="mx-chip mx-na"></i>task not on this repo</span>`
      + `<span class="meta">Each cell stacks repos S, M and L from top to bottom${state.round === "all" ? ", with thinking on on the left and off on the right" : ""}. Column labels: q = search queries written by the model, +h = reuse hint, emb = embeddings, hyb = hybrid, sel = selective, qw = Qwen Code tool names. ${(state.round || "final") === "final" ? " Under each PASS total, the change against thinking off is given, counted over the runs both rounds have (columns in percentage points)." : ""} At L0–L2, the harness runs each task once, on the smallest repo it fits. A run is opened by clicking its block, and compared by Shift-clicking.</span>`;
  }

  function fillFilters() {
    const opts = (vals, cur, all) => `<option value="">${all}</option>` + vals.map((v) => `<option ${v === cur ? "selected" : ""}>${esc(v)}</option>`).join("");
    const uniq = (k) => [...new Set(runs.map((r) => r[k]))].sort();
    $("#f-task").innerHTML = opts(uniq("task_id"), state.task, "All tasks");
    $("#f-level").innerHTML = opts(uniq("level_label").sort(byLabel), state.level, "All levels");
    $("#f-label").innerHTML = opts(uniq("outcome"), state.label, "Any outcome");
    $("#f-round").value = state.round;
    $("#f-q").value = state.q;
  }

  // ---------- one run ----------
  async function load(id) {
    if (!cache.has(id)) cache.set(id, fetch(`raw/runs/${id}.json`).then((r) => r.json()));
    return cache.get(id);
  }

  // a segment's size in characters; a measured token count (s.tokens: L-AG's classified harness prompt) wins
  const segChars = (s) => (s.tokens != null ? s.tokens * 3.6 : s.text.length);
  const segTok = (s) => (s.tokens != null ? s.tokens : tok(s.text.length));

  function compBar(run) {
    const segs = [...run.system, ...run.user];
    const total = segs.reduce((s, x) => s + segChars(x), 0) || 1;
    const parts = segs.map((s) => `<div class="k-${s.kind}" style="flex:${segChars(s) / total}" title="${esc(s.label)}: ≈${fmt(segTok(s))} tok"></div>`).join("");
    return `<div class="comp-wrap"><div class="comp" role="img" aria-label="Prompt composition">${parts}</div>
      <div class="meta"><span>initial prompt ≈${fmt(tok(total))} tok (est. from characters)</span><span>${segs.length} parts</span></div></div>`;
  }

  function segHTML(s) {
    const open = OPEN_BY_DEFAULT.has(s.kind) && s.text.length < 6000 ? " open" : "";
    return `<details class="seg k-${s.kind}"${open}><summary><span class="lab">${esc(s.label)}</span><span class="sz">≈${fmt(segTok(s))} tok</span></summary><pre>${esc(s.text)}</pre></details>`;
  }

  function promptHTML(run) {
    return `${compBar(run)}<div class="segs"><div class="group-label">System message</div>${run.system.map(segHTML).join("")}
      <div class="group-label">User message</div>${run.user.map(segHTML).join("")}</div>`;
  }

  // Patch: side by side per file (old left, new right), removed and added lines paired row by row,
  // highlighted per line. A created or deleted file gets one column.
  function parseDiff(text) {
    const files = [];
    let f = null, h = null, oldNo = 0, newNo = 0, dels = [], adds = [];
    const flush = () => {
      if (!h) return;
      for (let i = 0; i < Math.max(dels.length, adds.length); i++) h.rows.push({o: dels[i] || null, n: adds[i] || null, kind: "chg"});
      dels = []; adds = [];
    };
    for (const line of text.split("\n")) {
      if (line.startsWith("--- ")) { flush(); f = {old: line.slice(4).replace(/^a\//, ""), path: "", hunks: [], add: 0, del: 0}; files.push(f); h = null; continue; }
      if (line.startsWith("+++ ") && f && !f.path) { f.path = line.slice(4).replace(/^b\//, ""); continue; }
      const m = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$/);
      if (m && f) { flush(); oldNo = +m[1]; newNo = +m[2]; h = {head: line, rows: []}; f.hunks.push(h); continue; }
      if (!h || line.startsWith("\\")) continue;           // "\ No newline at end of file"
      if (line.startsWith("-")) { dels.push({no: oldNo++, text: line.slice(1)}); f.del++; continue; }
      if (line.startsWith("+")) { adds.push({no: newNo++, text: line.slice(1)}); f.add++; continue; }
      flush();
      const t = line.slice(1);
      h.rows.push({o: {no: oldNo++, text: t}, n: {no: newNo++, text: t}, kind: "ctx"});
    }
    flush();
    return files;
  }

  function hl(text, lang) {
    if (!window.hljs || lang === "plaintext") return esc(text);
    try { return window.hljs.highlight(text, {language: lang, ignoreIllegals: true}).value; } catch (e) { return esc(text); }
  }

  // Highlights consecutive lines as one text, so constructs spanning lines (docstrings, multi-line strings)
  // keep their colours, then splits the result back into lines: spans open at a line break are closed at
  // its end and reopened on the next line. hljs escapes the code, so every "<" in its output is a tag.
  function hlLines(texts, lang) {
    if (!window.hljs || lang === "plaintext") return texts.map(esc);
    if (!texts.length) return [];                         // "".split("\n") would still give one line
    const out = [], open = [];
    for (const line of hl(texts.join("\n"), lang).split("\n")) {
      const start = open.join("");
      for (const m of line.matchAll(/<span[^>]*>|<\/span>/g)) m[0][1] === "/" ? open.pop() : open.push(m[0]);
      out.push(start + line + "</span>".repeat(open.length));
    }
    return out;
  }

  // A file view (L-AG's view_file) as a code browser panel: the path and line range as its header, then numbered,
  // highlighted lines. Antigravity's ranges are 0-based and inclusive; the panel numbers lines from 1.
  function codeViewHTML(path, start, end, text, note) {
    const lang = LANG[(String(path).split(".").pop() || "").toLowerCase()] || "plaintext";
    const first = Number(start) + 1, lines = text == null ? null : text.split("\n");
    // the lines actually shown (a range asked past the file's end is cut there); without content, the range asked for
    const last = lines ? first + lines.length - 1 : (end === "None" || end == null ? "end" : Number(end) + 1);
    const head = `<div class="cv-head"><span class="cv-path">${esc(path)}</span><span class="cv-range">lines ${first}–${last}</span>${note ? `<span class="cv-note">${esc(note)}</span>` : ""}</div>`;
    if (lines == null) return `<div class="codeview">${head}</div>`;
    const rows = hlLines(lines, lang).map((h, i) => `<tr><td class="ln">${first + i}</td><td class="code">${h || " "}</td></tr>`).join("");
    return `<div class="codeview">${head}<div class="cv-body"><table class="cv"><colgroup><col class="n"><col></colgroup><tbody>${rows}</tbody></table></div></div>`;
  }

  // One file of a diff, side by side (the Diff tab and edit calls): f = {old, path, hunks, add, del}.
  // Each side of a hunk is highlighted as one block (a hunk that starts inside a string can still be off).
  function fileDiffHTML(f, label) {
    const created = f.old === "/dev/null", deleted = f.path === "/dev/null";
    const path = deleted ? f.old : f.path;
    const lang = LANG[(path.split(".").pop() || "").toLowerCase()] || "plaintext";
    const one = created || deleted;
    for (const h of f.hunks) for (const k of ["o", "n"]) {
      const sides = h.rows.map((r) => r[k]).filter(Boolean);
      hlLines(sides.map((s) => s.text), lang).forEach((html, i) => { sides[i].html = html; });
    }
    const cell = (side, cls) => side ? `<td class="ln">${side.no}</td><td class="code ${cls}">${side.html || " "}</td>` : `<td class="ln"></td><td class="code none"></td>`;
    const rows = f.hunks.map((h) => (h.head ? `<tr class="hunk"><td colspan="${one ? 2 : 4}">${esc(h.head)}</td></tr>` : "") + h.rows.map((r) => {
      if (one) return `<tr>${cell(created ? r.n : r.o, created ? "add" : "del")}</tr>`;
      return `<tr>${cell(r.o, r.kind === "chg" ? "del" : "")}${cell(r.n, r.kind === "chg" ? "add" : "")}</tr>`;
    }).join("")).join("");
    const tag = created ? " · new file" : deleted ? " · deleted" : "";
    return `<details class="seg k-file" open><summary><span class="lab">${esc(label || path)}${tag}</span><span class="sz"><span class="add">+${f.add}</span> <span class="del">−${f.del}</span></span></summary>
        <div class="sbs-wrap"><table class="sbs${one ? " one" : ""}"><colgroup>${one ? "<col class=n><col>" : "<col class=n><col><col class=n><col>"}</colgroup><tbody>${rows}</tbody></table></div></details>`;
  }

  function diffHTML(text) {
    if (!text) return `<p class="empty">No patch (no valid file blocks).</p>`;
    const files = parseDiff(text);
    if (!files.length) return `<pre class="block">${esc(text)}</pre>`;
    return `<div class="answer-files">` + files.map((f) => fileDiffHTML(f)).join("") + `</div>`;
  }

  // An edit's old and new text as one diff hunk (longest common subsequence of lines; line numbers count
  // within the snippet, since an edit call doesn't say where in the file it lands).
  function lineDiff(oldT, newT) {
    const a = oldT.split("\n"), b = newT.split("\n"), n = a.length, m = b.length;
    const L = Array.from({length: n + 1}, () => new Int32Array(m + 1));
    for (let i = n - 1; i >= 0; i--) for (let j = m - 1; j >= 0; j--)
      L[i][j] = a[i] === b[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
    const rows = [];
    let i = 0, j = 0, dels = [], adds = [], add = 0, del = 0;
    const flush = () => { for (let k = 0; k < Math.max(dels.length, adds.length); k++) rows.push({o: dels[k] || null, n: adds[k] || null, kind: "chg"}); dels = []; adds = []; };
    while (i < n || j < m) {
      if (i < n && j < m && a[i] === b[j]) { flush(); rows.push({o: {no: i + 1, text: a[i]}, n: {no: j + 1, text: b[j]}, kind: "ctx"}); i++; j++; }
      else if (j < m && (i >= n || L[i][j + 1] >= L[i + 1][j])) { adds.push({no: j + 1, text: b[j]}); add++; j++; }
      else { dels.push({no: i + 1, text: a[i]}); del++; i++; }
    }
    flush();
    return {hunks: [{head: "", rows}], add, del};
  }


  // A tool result in parts: selective/L8 segments start with "[#n] label"; rich read_symbol and
  // semantic_search output starts each part with "path:a-b  name", "File: path (lines ...", "Imports of
  // path:" or a "(...)" note after a blank line. One part = the whole result.
  function splitResult(content, tool) {
    const lines = content.split("\n");
    const idRe = /^\[#(\d+)\]/;
    const headRe = /^(\S+:\d+-\d+  \S|File: \S+ \(lines|Imports of \S+:|\((no |read lines|output capped|at most))/;
    const byId = lines.some((l) => idRe.test(l));
    if (!byId && !["read_symbol", "semantic_search"].includes(tool)) return [{head: "", text: content, id: null}];
    const parts = [];
    lines.forEach((l, i) => {
      const starts = byId ? idRe.test(l) : headRe.test(l) && (i === 0 || lines[i - 1].trim() === "");
      if (starts || !parts.length) parts.push({lines: [l]});
      else parts[parts.length - 1].lines.push(l);
    });
    return parts.map((p) => {
      const text = p.lines.join("\n").replace(/\n+$/, "");
      const m = text.match(idRe);
      const head = text.split("\n")[0].replace(/^\[#\d+\]\s*/, (s) => s).slice(0, 140);
      return {head, text, id: m ? Number(m[1]) : null};
    });
  }

  // A reply's complete <file path="..."> blocks become collapsible, highlighted code (open by default); text
  // around them stays as it is. A reply without a complete block renders as plain text, unchanged.
  const FILE_RX = /<file path="([^"]+)">\n?([\s\S]*?)\n?<\/file>/g;
  const LANG = {py: "python", sql: "sql", html: "xml", toml: "ini", md: "markdown", json: "json", yaml: "yaml", yml: "yaml"};
  function answerHTML(text) {
    const parts = [];
    let last = 0, m;
    FILE_RX.lastIndex = 0;
    while ((m = FILE_RX.exec(text))) {
      const before = text.slice(last, m.index).trim();
      if (before) parts.push(`<pre class="block">${esc(before)}</pre>`);
      const lang = LANG[(m[1].split(".").pop() || "").toLowerCase()] || "plaintext";
      const lines = m[2] ? m[2].split("\n").length : 0;
      parts.push(`<details class="seg k-file" open><summary><span class="lab">File · ${esc(m[1])}</span><span class="sz">${lines} line${lines === 1 ? "" : "s"}</span></summary><pre class="code"><code class="language-${lang}">${esc(m[2])}</code></pre></details>`);
      last = FILE_RX.lastIndex;
    }
    if (!parts.length) return `<pre class="block">${esc(text)}</pre>`;
    const after = text.slice(last).trim();
    if (after) parts.push(`<pre class="block">${esc(after)}</pre>`);
    return `<div class="answer-files">${parts.join("")}</div>`;
  }

  // One tool call: the tool and what it targets on one line, the raw parameters collapsed, and any code it
  // carries (a new file's content, an edit's old and new text) as highlighted file text. Both vocabularies:
  // the harness's (path, old, new) and Qwen Code's (file_path, old_string, new_string).
  // A shell command as a terminal line: the working directory as the prompt, the command as written (multi-line
  // scripts keep their lines).
  function termCommandHTML(cmd, cwd) {
    return `<pre class="term"><span class="cwd">${esc(cwd || "")}</span><span class="prompt">$ </span>${esc(cmd)}</pre>`;
  }

  // run_command's result as the viewer gets it ("$ command\nexit N\n\noutput"): {exit, html} with the exit code for
  // the result's header and the output as a terminal panel.
  function termResult(text) {
    const m = String(text).match(/^\$ ([\s\S]*?)\nexit (\S+)\n\n?([\s\S]*)$/);
    if (!m) return {exit: null, html: `<pre class="term"><span class="out">${esc(text)}</span></pre>`};
    return {exit: m[2], html: `<pre class="term"><span class="out">${esc(m[3] || "(no output)")}</span></pre>`};
  }

  function callHTML(c) {
    let a = null;
    try { a = JSON.parse(c.arguments || "{}"); } catch (e) { a = null; }
    if (!a || typeof a !== "object" || Array.isArray(a)) return `<div class="call"><b>${esc(c.name)}</b> ${esc(c.arguments)}</div>`;
    if (c.name === "run_command" && typeof a.command_line === "string") {
      const params = `<details class="params"><summary>parameters</summary><pre>${esc(JSON.stringify(a, null, 2))}</pre></details>`;
      return `<div class="call"><div><b>run_command</b></div>${termCommandHTML(a.command_line, a.working_dir ? a.working_dir + " " : "")}${params}</div>`;
    }
    const path = a.path || a.file_path || "";
    const lang = LANG[(String(path).split(".").pop() || "").toLowerCase()] || "plaintext";
    const code = (label, text) => `<details class="seg k-file" open><summary><span class="lab">${esc(label)}</span><span class="sz">${String(text).split("\n").length} lines</span></summary><pre class="code"><code class="language-${lang}">${esc(text)}</code></pre></details>`;
    const blocks = [];
    if (typeof a.content === "string") blocks.push(code(path || "content", a.content));
    const oldT = a.old ?? a.old_string, newT = a.new ?? a.new_string;
    if (typeof oldT === "string" && typeof newT === "string")       // an edit: side by side, like the Diff tab
      blocks.push(fileDiffHTML({old: path || "edit", path: path || "edit", ...lineDiff(oldT, newT)}, `${path} · edit`));
    const key = ["pattern", "query", "symbol", "symbols", "ids", "path", "file_path", "directory"].find((k) => k in a);
    const v = key ? a[key] : "";
    const target = Array.isArray(v) ? v.map((x) => (key === "ids" ? "#" + x : x)).join(", ") : String(v);
    const shown = new Set([key, "content", "old", "new", "old_string", "new_string"]);
    const extra = Object.entries(a).filter(([k]) => !shown.has(k))
      .map(([k, x]) => `${k}=${typeof x === "string" ? x : JSON.stringify(x)}`).join(" ");
    const params = `<details class="params"><summary>parameters</summary><pre>${esc(JSON.stringify(a, null, 2))}</pre></details>`;
    return `<div class="call"><div><b>${esc(c.name)}</b> ${esc(target)}${extra && extra.length < 120 ? ` <span class="small">${esc(extra)}</span>` : ""}</div>${blocks.join("")}${params}</div>`;
  }

  function transcriptHTML(run) {
    const t = run.transcript || [];
    if (!t.length) return run.response ? answerHTML(run.response) : `<p class="empty">The harness saved no answer for this run.</p>`;
    const names = {}, droppedIds = new Set();
    t.forEach((m) => (m.tool_calls || []).forEach((c) => (names[c.id] = c.name)));
    t.forEach((m) => (m.drop || []).forEach((id) => droppedIds.add(id)));
    // One block per model turn, then one "Harness" block for everything the harness sends back (tool
    // results, harness messages) until the next model turn, so the two sides never blur together.
    const out = [];
    let harness = [], turnNo = 0;
    const flushHarness = () => {
      if (!harness.length) return;
      const n = harness.filter((h) => h.tool).length;
      const head = n ? `Harness → model · ${n} tool result${n > 1 ? "s" : ""}` : "Harness → model";
      out.push(`<div class="harness"><div class="who">${head}</div>${harness.map((h) => h.html).join("")}</div>`);
      harness = [];
    };
    for (const m of t) {
      if (m.role === "step") {
        flushHarness();
        out.push(`<div class="turn"><div class="who">Planning call (${esc(m.step)})</div>
          <details class="seg k-tools"><summary><span class="lab">Instruction</span></summary><pre>${esc(m.instruction)}</pre></details>
          <details class="seg k-tools" open><summary><span class="lab">Model's queries</span></summary><pre>${esc(m.reply)}</pre></details>
          <details class="seg k-retrieved"><summary><span class="lab">Hits per query</span></summary><pre>${esc(m.hits)}</pre></details></div>`);
        continue;
      }
      if (m.role === "tool" && m.drop) {
        harness.push({tool: true, html: `<div class="call"><b>drop</b> ${esc(m.content)}</div>`});
        continue;
      }
      if (m.role === "tool") {
        const tool = names[m.tool_call_id] || "tool";
        // L8 appends "[n of 15 points left]" to a turn's last result: shown as its own item here (the
        // message the model got is unchanged)
        const pts = (m.content || "").match(/\n*\[(\d+(?:\.\d+)?) of (\d+) points left\]\s*$/);
        const content = pts ? m.content.slice(0, pts.index) : (m.content || "");
        const pointsItem = pts ? {tool: false, html: `<div class="points-left">${esc(pts[1])} of ${esc(pts[2])} points left</div>`} : null;
        const parts = splitResult(content, tool);
        const gone = (m.result_ids || []).filter((id) => droppedIds.has(id));
        const all = gone.length && gone.length === m.result_ids.length;
        if (parts.length > 1) {                    // one item per definition / range / imports / note
          const items = parts.map((p) => {
            const dropped = p.id != null && droppedIds.has(p.id);
            return `<details class="seg ${dropped ? "k-closing" : "k-tools"}"><summary><span class="lab">${esc(p.head)}${dropped ? " · dropped later" : ""}</span><span class="sz">≈${fmt(tok(p.text.length))} tok</span></summary><pre>${esc(p.text)}</pre></details>`;
          }).join("");
          harness.push({tool: true, html: `<div class="result-group"><div class="lab">Result · ${esc(tool)} · ${parts.length} parts</div>${items}</div>`});
          if (pointsItem) harness.push(pointsItem);
          continue;
        }
        const view = tool === "view_file" && content.match(/^(.+?), lines (\d+)-(\S+) \((as in the repository before the change|a file the agent created or changed; content not recorded)\)(?:\n\n([\s\S]*))?$/);
        if (view) {                                // L-AG's file views: a code browser panel
          const before = view[4].startsWith("as in");
          const panel = codeViewHTML(view[1], view[2], view[3], before ? (view[5] ?? "") : null,
                                     before ? "as before the change" : "created or changed by the agent; content not recorded");
          harness.push({tool: true, html: `<details class="seg k-tools" open><summary><span class="lab">Result · view_file</span><span class="sz">≈${fmt(tok(content.length))} tok</span></summary>${panel}</details>`});
          continue;
        }
        if (tool === "run_command" && /^\$ /.test(content)) {      // L-AG's shell: the output as a terminal panel
          const r = termResult(content);
          const ex = r.exit == null ? "" : ` · <span class="${r.exit === "0" ? "exit-ok" : "exit-bad"}">exit ${esc(r.exit)}</span>`;
          harness.push({tool: true, html: `<details class="seg k-tools" open><summary><span class="lab">Result · run_command${ex}</span><span class="sz">≈${fmt(tok(content.length))} tok</span></summary>${r.html}</details>`});
          continue;
        }
        const note = gone.length ? ` · ${all ? "dropped later" : "partly dropped later"} (${gone.map((i) => "#" + i).join(", ")}; stubs sent from then on)` : "";
        harness.push({tool: true, html: `<details class="seg ${all ? "k-closing" : "k-tools"}"><summary><span class="lab">Result · ${esc(tool)}${note}</span><span class="sz">≈${fmt(tok(content.length))} tok</span></summary><pre>${esc(content)}</pre></details>`});
        if (pointsItem) harness.push(pointsItem);
        continue;
      }
      if (m.role === "user") {
        harness.push({tool: false, html: `<details class="seg k-closing" open><summary><span class="lab">Harness message</span></summary><pre>${esc(m.content)}</pre></details>`});
        continue;
      }
      flushHarness();
      turnNo += 1;
      const calls = (m.tool_calls || []).map(callHTML).join("");
      const text = (m.content || "").trim();
      const body = text ? `<div class="model-text"><div class="lab">${calls ? "Text written with the tool calls" : "Text"}</div>${answerHTML(text)}</div>` : "";
      const callsHTML = calls ? `<div class="lab">Tool calls</div>${calls}` : (text ? "" : `<p class="empty">Empty reply.</p>`);
      const thought = m.reasoning ? `<details class="seg k-closing"><summary><span class="lab">Thinking</span><span class="sz">≈${fmt(tok(m.reasoning.length))} tok</span></summary><pre>${esc(m.reasoning)}</pre></details>` : "";
      out.push(`<div class="turn model"><div class="who">Model · turn ${turnNo}${m.finish_reason ? ` · ${esc(m.finish_reason)}` : ""}</div>${thought}${body}${callsHTML}</div>`);
    }
    flushHarness();
    return `<div class="segs">` + out.join("") + `</div>`;
  }

  function paneHTML(run, side) {
    const m = run.meta;
    const facts = [
      ["round", m.round === "final" ? "thinking on" : "thinking off"],
      ["tests", frac(m.tests_passed, m.tests_total)],
      ["reuse", m.reuse_passed == null && m.category === "reuse" ? "n/a (code didn't run)" : yn(m.reuse_passed)],
      ["repo tests", frac(m.repo_tests_passed, m.repo_tests_total)], ["format", m.format_status],
      ["patch run by tests", m.patch_coverage == null ? "–" : `${Math.round(100 * m.patch_coverage)}% of ${m.patch_stmts}`],
      ["tests added", m.tests_added ?? "–"],
      ["judge: ranked above it", m.grade_rank_pct == null ? "–" : `${Math.round(100 * m.grade_rank_pct)}% of ${m.grade_candidates - 1} others`],
      ["judge correctness", m.grade_correctness ?? "–"],
      ...(m.penalty_points == null ? [] : [["penalty points", m.penalty_points]]),
      ["in", fmt(m.tokens_in)], ["cached", fmt(m.tokens_cached)], ["out", fmt(m.tokens_out)],
      ["sec", m.latency_s == null ? "–" : Math.round(m.latency_s)], ["tools", m.tool_calls ?? 0],
      ["helper seen", m.helper_body_in_context ? "body" : m.helper_in_context ? "signature" : "no"],
      ...(m.drops == null ? [] : [["dropped", `${m.drops} (${m.drop_calls} calls)`],
        ["helper body at answer", yn(m.helper_body_at_answer)]]),
    ].map(([k, v]) => `<span>${k} <b>${esc(v)}</b></span>`).join("");
    let body;
    switch (state.tab) {
      case "prompt": body = promptHTML(run); break;
      case "thinking": body = run.reasoning ? `<pre class="block">${esc(run.reasoning)}</pre>` : `<p class="empty">No thinking (thinking was off, or the model returned none).</p>`; break;
      case "answer": body = transcriptHTML(run); break;
      case "patch": body = diffHTML(run.patch); break;
      case "tests": body = (run.tests ? `<pre class="block">${esc(run.tests)}</pre>` : `<p class="empty">No test output.</p>`)
        + (run.judge_note ? `<div class="group-label">Judge's note (blind comparison, rubric v2)</div><pre class="block">${esc(run.judge_note)}</pre>` : "")
        + (m.penalty_points == null ? "" : `<div class="group-label">Penalty judge (${esc(m.penalty_judge)}): ${m.penalty_points} points</div>`
          + (run.penalties && run.penalties.length
              ? run.penalties.map(p => `<pre class="block"><b>${esc(p.code)} ${p.points}</b>  ${esc(p.quote)}\n${esc(p.why)}</pre>`).join("")
              : `<p class="empty">No penalties.</p>`)); break;
    }
    const bd = run.badges || {exact: [], intent: []};
    const chips = (bd.exact || []).map((b) => `<span class="badge exact">${esc(b)}</span>`).join("")
      + (bd.intent || []).map((b) => `<span class="badge intent ${b.confidence}" title="automatic (embedding match, unverified): ${esc(b.sentence || "")}${b.variant_example ? `&#10;closest kind of dodge: ${esc(b.variant_example)}` : ""}">${esc(b.badge)} · ${esc(b.confidence)}</span>`).join("");
    const badgesHTML = chips ? `<div class="badges">${chips}</div>` : "";
    const close = side === "b" ? `<button class="pick" data-close="b">close</button>` : "";
    return `<div class="pane ${side}"><div class="pane-head"><h3>${side.toUpperCase()} · ${esc(m.task_id)} · ${esc(m.level_label)} · ${esc(m.repo_size)} ${badge(m.outcome)} ${close}</h3>${badgesHTML}
      <div class="facts">${facts}</div><div class="meta">run <a href="#a=${m.run_id}">${m.run_id}</a> · target ${esc(m.target_file)}</div></div>${body}</div>`;
  }

  function twinOptions(run) {
    // other runs of the same task: the quickest way to pick a comparison
    const m = run.meta;
    const twins = runs.filter((r) => r.task_id === m.task_id && r.run_id !== m.run_id)
      .sort((x, y) => x.round.localeCompare(y.round) || byLabel(x.level_label, y.level_label) || x.repo_size.localeCompare(y.repo_size));
    return `<label>Compare A with <select id="twin"><option value="">same task, another run…</option>` +
      twins.map((r) => `<option value="${r.run_id}" ${r.run_id === state.b ? "selected" : ""}>${esc(r.level_label)} · ${r.repo_size} · ${r.round === "final" ? "think on" : "think off"} · ${r.outcome}</option>`).join("") +
      `</select></label>`;
  }

  async function renderPanes() {
    const el = $("#panes");
    $("#tabs").innerHTML = TABS.map(([k, t]) => `<button role="tab" data-tab="${k}" aria-selected="${state.tab === k}">${t}</button>`).join("");
    if (!state.a) {
      el.className = "panes";
      el.innerHTML = `<p class="empty">A run is opened by picking it in the table or the matrix. Two runs are compared side by side with <b>B</b> on another row, Shift-click in the matrix, or the menu that appears.</p>`;
      $("#twin-wrap").innerHTML = "";
      return;
    }
    const [a, b] = await Promise.all([load(state.a), state.b ? load(state.b) : null]);
    $("#twin-wrap").innerHTML = twinOptions(a);
    el.className = b ? "panes two" : "panes";
    el.innerHTML = paneHTML(a, "a") + (b ? paneHTML(b, "b") : "");
    if (window.hljs) el.querySelectorAll("pre.code code").forEach((c) => window.hljs.highlightElement(c));
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
    on("#views", "click", (e) => { const b = e.target.closest("[data-view]"); if (b) { state.view = b.dataset.view; render(); } });
    on("#runs", "click", (e) => {
      const chip = e.target.closest("[data-run]");
      if (chip) {
        const id = chip.dataset.run;
        if (e.shiftKey && state.a && id !== state.a) state.b = id === state.b ? null : id;
        else { state.a = id; if (state.b === id) state.b = null; }
        render();
        if (!e.shiftKey) $("#tabs").scrollIntoView({behavior: "smooth", block: "start"});
        return;
      }
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
    [runs] = await Promise.all([fetch("raw/index.json").then((r) => r.json()),
      fetch("raw/tasks.json").then((r) => r.json()).then((ts) => { tasks = new Map(ts.map((x) => [x.id, x])); }).catch(() => {})]);
    byId = new Map(runs.map((r) => [r.run_id, r]));
    if (state.a && !byId.has(state.a)) state.a = null;
    if (state.b && !byId.has(state.b)) state.b = null;
    fillFilters();
    wire();
    render();
  }
  init().catch((e) => { $("#panes").innerHTML = `<p class="empty">The data couldn't be loaded: ${esc(e.message)}</p>`; });
})();
