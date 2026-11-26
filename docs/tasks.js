// Tasks page: one card per task from data/tasks.json, with links into the raw-data viewer.
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const diff = (t) => esc(t).split("\n").map((l) => l.startsWith("@@") ? `<span class="hunk">${l}</span>` : l.startsWith("+") ? `<span class="add">${l}</span>` : l.startsWith("-") ? `<span class="del">${l}</span>` : l).join("\n");
  const SOL = { reference: "Reference solution (passes)", context_blind: "Written without the repository (fails)", trusting: "Trusts the docs (fails)" };
  let tasks = [];
  // A task prompt as flowing paragraphs: the line breaks of the task file's hard wrapping are joined, blank lines
  // between paragraphs kept, so the text fills its box at any width.
  const reflow = (text) => String(text).trim().split(/\n\s*\n/).map((p) => p.replace(/\s*\n\s*/g, " ")).join("\n\n");

  function card(t) {
    const helpers = t.helpers.length ? t.helpers.map((h) => `<code>${esc(h.qualname)}</code> <span class="meta">${esc(h.naming || "")}</span>`).join("<br>") : "none";
    const decoys = t.decoys.length ? t.decoys.map((d) => `<code>${esc(d.qualname)}</code>`).join("<br>") : "none";
    const tests = Object.entries(t.hidden_tests).map(([n, s]) => `<details><summary>${esc(n)}</summary><pre class="block">${esc(s)}</pre></details>`).join("");
    const sols = Object.entries(t.solutions).map(([n, s]) => `<details><summary>${esc(SOL[n] || n)}</summary><pre class="block diff">${diff(s)}</pre></details>`).join("");
    const d = t.difficulty;
    const chip = d ? `<span class="diff diff-${d.label}" title="${d.passed} of ${d.runs} runs pass (L0-L8, both rounds)">${d.label} · ${Math.round(100 * d.passed / d.runs)}% pass</span>` : "";
    const challenge = t.challenge ? `<p class="challenge"><b>The challenge.</b> ${esc(t.challenge.replace(/\s*\n\s*/g, " "))}</p>` : "";
    return `<article class="task-card" id="${esc(t.id)}"><h3>${esc(t.id)} ${chip} <span class="tag">${esc(t.category)}</span> <span class="meta">tier ${t.tier} · repo ${t.repo_sizes.join(", ")} · target <code>${esc(t.target_file)}</code></span></h3>
      ${challenge}
      <pre class="prompt task-prompt">${esc(reflow(t.prompt))}</pre>
      <div class="facts"><span>needs <b>${helpers}</b></span><span>decoys <b>${decoys}</b></span></div>
      ${t.notes ? `<div class="task-notes"><div class="lab">What the runs show</div>${t.notes.split(/\n\s*\n/).map((p) => `<p>${esc(p.replace(/\s*\n\s*/g, " "))}</p>`).join("")}</div>` : ""}
      ${tests}${sols}
      <p class="meta"><a href="data#round=final&task=${encodeURIComponent(t.id)}">Every run of this task →</a></p></article>`;
  }

  function render() {
    const cat = $("#t-cat").value, diff = $("#t-diff").value;
    const list = tasks.filter((t) => (!cat || t.category === cat) && (!diff || (t.difficulty && t.difficulty.label === diff)));
    $("#t-count").textContent = `${list.length} tasks`;
    $("#task-list").innerHTML = list.map(card).join("");
  }

  fetch("raw/tasks.json").then((r) => r.json()).then((d) => {
    tasks = d;
    $("#t-cat").innerHTML += [...new Set(d.map((t) => t.category))].sort().map((c) => `<option>${esc(c)}</option>`).join("");
    $("#t-cat").addEventListener("change", render);
    $("#t-diff").addEventListener("change", render);
    render();
  });
})();
