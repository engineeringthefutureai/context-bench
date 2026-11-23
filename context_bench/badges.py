"""Badges for runs (cbench badges): what a run's final answer says and does, for the report and viewer.

Two kinds, never mixed up:
- exact: read off the recorded run (format status, retries, warnings, tests added, dead code used). Certain.
- intent: what the final answer's prose says (declares completion, claims it verified, notes missing
  context, ...), by embedding similarity of each sentence to a few example phrases per label. Automatic
  and unverified: each comes with a confidence (high / medium) from calibrated similarity thresholds,
  and anything below them gets no badge.
Output: one line per run in results/badges.jsonl. Badges never change labels or outcomes.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np

# EmbeddingGemma's classification task format, on both the examples and the sentences (it separated the
# labels a little better than "sentence similarity" in calibration).
PREFIX = "task: classification | query: "

# label -> example phrases (what such a sentence typically says). Short, varied, task-neutral.
INTENTS: dict[str, list[str]] = {
    "DECLARES COMPLETION": [
        "The change is complete.", "Done.", "The implementation is finished.",
        "I have completed the requested change.", "Everything requested is now implemented.",
    ],
    "CLAIMS VERIFICATION": [
        "All tests pass.", "All checks pass.", "I verified that the code works.",
        "I tested it and it works correctly.", "The edit is verified in place.",
    ],
    "NOTES MISSING CONTEXT": [
        "The context provided does not contain the implementation of this module.",
        "The file I need is not shown, so I will assume how it works.",
        "Without seeing that code I have to make an assumption.",
        "The required helper is not included in the provided context.",
    ],
    "ASKS FOR INFORMATION": [
        "Could you share the contents of that file?", "Please provide the missing module.",
        "Which behaviour do you expect here?", "Can you clarify the requirement?",
    ],
    "WANTS TO EXPLORE": [
        "Let me start by exploring the repository.", "First I need to look at the existing code.",
        "I will search the codebase for the relevant function.", "Let me read that file first.",
    ],
    "STATES A PLAN": [
        "To satisfy the request, I need to do the following steps.", "Here is my plan.",
        "First I will update the model, then the serializer.", "I will make the following changes.",
    ],
    "CONCEDES": [
        "I cannot complete this task.", "I am unable to implement this without more information.",
        "This is not possible with what I have.", "I could not finish the change.",
    ],
    "LEAVES WORK UNFINISHED": [
        "The rest is left as a TODO.", "This part still needs to be implemented.",
        "I only implemented part of the change.", "Remaining work: update the other callers.",
    ],
    "STATES REUSE": [
        "I reuse the existing helper instead of writing a new one.",
        "The shop already has a function for this, so I call it.",
        "This uses the same function the rest of the code uses.",
    ],
}

_BLOCKS = [re.compile(r"<file\b.*?</file>", re.S), re.compile(r"<(edit|tool_call)\b.*?</\1>", re.S),
           re.compile(r"```.*?```", re.S)]


def final_prose(row: dict, run_dir: Path) -> str:
    """The final answer without code: L7/L8 the last reply without tool calls, else the response."""
    resp = run_dir / "response.txt"
    text = resp.read_text() if resp.is_file() else ""
    tr = run_dir / "transcript.jsonl"
    if row.get("level") in ("L7", "L8") and tr.is_file():
        msgs = [json.loads(l) for l in tr.read_text().splitlines() if l.strip()]
        last = [m for m in msgs if m.get("role") == "assistant" and not m.get("tool_calls")]
        if last:
            text = last[-1].get("content") or text
    for rx in _BLOCKS:
        text = rx.sub(" ", text)
    return " ".join(text.split())


def sentences(text: str) -> list[str]:
    """Rough sentence split (also on list items); fragments under 12 characters are dropped."""
    parts = re.split(r"(?<=[.!?])\s+|\s+(?=\d+\.\s)|\s+[-*]\s+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 12][:40]


def exact_badges(row: dict, run_dir: Path) -> list[str]:
    out = []
    fs = row.get("format_status")
    if fs == "no_code":
        out.append("NO CODE")
    elif fs == "tool_request":
        out.append("PRETEND TOOL CALL")
    elif fs == "bad_format":
        out.append("BAD FORMAT")
    if "max_output_tokens" in (row.get("error") or ""):
        out.append("OUT OF TOKENS")
    if row.get("format_retry"):
        out.append("RETRIED: FORMAT")
    tr = run_dir / "transcript.jsonl"
    if tr.is_file() and "These files you changed don't parse" in tr.read_text():
        out.append("RETRIED: SYNTAX")
    tests = run_dir / "tests.txt"
    if tests.is_file() and re.search(r"\b(SyntaxError|IndentationError)\b", tests.read_text()):
        out.append("SYNTAX ERROR IN PATCH")
    if row.get("tests_added"):
        out.append("TESTS ADDED")
    if row.get("legacy_used"):
        out.append("USES DEAD CODE")
    return out


# Calibrated on the archived v4 runs (results/archive-v4-qwen3emb, 2026-11-23). EmbeddingGemma's similarities
# sit in a narrow high band (median ~0.8 against every label), so absolute thresholds don't separate; the rule
# is relative: each sentence goes to its best-matching label only if it beats the runner-up label by MARGIN_*.
# At >= 0.10 the sampled hits were all right, at 0.06-0.10 mostly right for the common labels; the rare ones
# (no real cases in the calibration data, wrong hits at low margins) only count at high.
MARGIN_HIGH, MARGIN_MEDIUM = 0.10, 0.06
HIGH_ONLY = {"CONCEDES", "LEAVES WORK UNFINISHED", "ASKS FOR INFORMATION"}


class IntentScorer:
    def __init__(self, embedder):
        self.embedder = embedder
        self.labels = list(INTENTS)
        protos = [(lab, p) for lab in self.labels for p in INTENTS[lab]]
        self.cols = [[j for j, (lab2, _) in enumerate(protos) if lab2 == lab] for lab in self.labels]
        self.proto_vecs = embedder.embed([PREFIX + p for _, p in protos])

    def per_sentence(self, text: str) -> list[tuple[str, str, float, float]]:
        """(sentence, best label, its similarity, margin over the runner-up label) per sentence."""
        sents = sentences(text)
        if not sents:
            return []
        sims = self.embedder.embed([PREFIX + s for s in sents]) @ self.proto_vecs.T
        per = np.stack([sims[:, c].max(1) for c in self.cols], 1)          # sentence x label
        order = np.argsort(-per, 1)
        out = []
        for i, s in enumerate(sents):
            a, b = order[i, 0], order[i, 1]
            out.append((s, self.labels[a], float(per[i, a]), float(per[i, a] - per[i, b])))
        return out

    def badges(self, text: str) -> list[dict]:
        """One badge per label: its best sentence, if that sentence clears the margin."""
        best: dict[str, tuple[float, float, str]] = {}
        for sent, lab, sim, margin in self.per_sentence(text):
            if lab not in best or margin > best[lab][0]:
                best[lab] = (margin, sim, sent)
        found = []
        for lab, (margin, sim, sent) in sorted(best.items(), key=lambda kv: -kv[1][0]):
            if margin >= MARGIN_HIGH or (margin >= MARGIN_MEDIUM and lab not in HIGH_ONLY):
                found.append({"badge": lab, "confidence": "high" if margin >= MARGIN_HIGH else "medium",
                              "margin": round(margin, 3), "sentence": sent[:200]})
        return found


def compute(rows: list[dict], runs_dir: Path, embedder) -> list[dict]:
    """One record per run: exact badges and intent badges of its final prose."""
    scorer = IntentScorer(embedder)
    out = []
    for r in rows:
        d = runs_dir / r["run_id"]
        prose = final_prose(r, d)
        out.append({"run_id": r["run_id"], "exact": exact_badges(r, d),
                    "intent": scorer.badges(prose) if len(prose) >= 20 else []})
    return out
