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
import random
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


LOOP_REPEATS = 3


def looped(transcript: Path) -> bool:
    """An agent called the identical tool (same name, same arguments) at least LOOP_REPEATS times."""
    seen: dict[str, int] = {}
    for line in transcript.read_text().splitlines():
        if not line.strip():
            continue
        for c in json.loads(line).get("tool_calls") or []:
            fn = c.get("function", c)
            key = f'{fn.get("name")}|{fn.get("arguments")}'
            seen[key] = seen.get(key, 0) + 1
            if seen[key] >= LOOP_REPEATS:
                return True
    return False


def second_chance(transcript: Path) -> bool:
    """The agent tried to finish with an answer the harness rejected and was asked once more (l7_agentic):
    no usable <file> blocks or no edits (FORMAT_RETRY, FORMAT_RETRY_EDIT), changed files that don't parse
    (SYNTAX_RETRY, L8), a tool call after the tools were gone (FINAL_RETRY), or a tail check that failed
    (TAIL_SYNTAX: the answer's files don't compile, L7; TAIL_TESTS: the repo's tests fail, L8)."""
    if not transcript.is_file():
        return False
    from .levels.l7_agentic import FINAL_RETRY, FORMAT_RETRY, FORMAT_RETRY_EDIT, SYNTAX_RETRY, TAIL_SYNTAX, TAIL_TESTS
    heads = (SYNTAX_RETRY.split("{problems}")[0], TAIL_SYNTAX.split("{problems}")[0], TAIL_TESTS.split("(")[0])
    for line in transcript.read_text().splitlines():
        try:
            m = json.loads(line)
        except ValueError:
            continue
        c = m.get("content") if isinstance(m, dict) and m.get("role") == "user" else None
        if isinstance(c, str) and (c in (FINAL_RETRY, FORMAT_RETRY, FORMAT_RETRY_EDIT) or c.startswith(heads)):
            return True
    return False


def exact_badges(row: dict, run_dir: Path) -> list[str]:
    out = []
    fs = row.get("format_status")
    if fs == "no_code":
        out.append("NO CODE")
    elif fs == "tool_request":
        out.append("PRETEND TOOL CALL")
    elif fs == "bad_format":
        out.append("BAD FORMAT")
    elif fs == "empty":
        out.append("EMPTY REPLY")
    if "max_output_tokens" in (row.get("error") or ""):
        out.append("OUT OF TOKENS")
    tr = run_dir / "transcript.jsonl"
    if row.get("format_retry") or (row.get("level") in ("L7", "L8") and second_chance(tr)):
        out.append("SECOND CHANCE")
    if tr.is_file() and row.get("level") in ("L7", "L8") and looped(tr):
        out.append("LOOPED")
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


# WANTS TO EXPLORE is measured, not matched to example phrases (docs/labeling-design.md). Prototypes on both
# sides: K_DODGE centroids of real "let me look first" replies (their variants: a generic "let me explore",
# "I'll look at the relevant files", "let me find how X is done", a pretend tool call ...) and K_ANSWER
# centroids of the prose that comes with real answers ("Creating the module now:", plans, "the task is
# clear"). A reply scores (similarity to its nearest dodge prototype) - (similarity to its nearest answer
# prototype). One dodge centroid blurred the variants and, with nothing to compare against, matched any short
# sentence about the next step. Built and calibrated by build_dodge_prototypes (cbench badges
# --rebuild-prototypes); specific to the embedder and prefix it was built with.
DODGE_PREFIX = "task: clustering | query: "
PROTOTYPES = Path(__file__).resolve().parent.parent / "grading" / "dodge_prototypes"     # .npz and .json
K_DODGE, K_ANSWER = 8, 3

# Inside a reply that did answer, look-first sentences are rare and the prototypes still miss or mistake a few
# (a typo fix, "creating the file now"), so there the badge also needs an explicit look-first phrase.
LOOK_FIRST = re.compile(r"\b(let me|i'll|i will|i need to|first,? i)\b[^.?!]{0,60}?\b(look|explore|check|search|"
                        r"read|see|inspect|examine|investigate|find)\b|\bi need to (know|understand|see)\b", re.I)


def is_dodge(r: dict) -> bool:
    """The ASKED TO EXPLORE outcome: no code, asked to look at the repository first."""
    return r.get("format_status") in ("no_code", "tool_request") and r.get("label") != "tool_error"


def dodge_text(r: dict, run_dir: Path) -> str:
    """What the prototypes compare: the final prose; for a reply without valid code and without prose (a bare
    pretend tool call, fenced code), the start of the reply."""
    prose = final_prose(r, run_dir)
    if len(prose) >= 20 or r.get("format_status") not in ("no_code", "tool_request", "bad_format"):
        return prose[:2000]                  # an answer that is all code has nothing to compare
    resp = run_dir / "response.txt"
    return (resp.read_text()[:600] if resp.is_file() else "")


def _unit(m: np.ndarray) -> np.ndarray:
    return m / np.maximum(np.linalg.norm(m, axis=-1, keepdims=True), 1e-12)


def _kmeans(v: np.ndarray, k: int, restarts: int = 15) -> np.ndarray:
    """Cosine k-means, fixed seeds (a rebuild gives the same prototypes); the best of `restarts` runs."""
    k = min(k, len(v))
    best = None
    for seed in range(restarts):
        c = v[np.random.default_rng(seed).choice(len(v), k, replace=False)]
        for _ in range(50):
            a = np.argmax(v @ c.T, 1)
            c = _unit(np.stack([v[a == j].mean(0) if (a == j).any() else c[j] for j in range(k)]))
        fit = float(np.mean(np.max(v @ c.T, 1)))
        if best is None or fit > best[0]:
            best = (fit, c)
    return best[1]


def _score(x: np.ndarray, dodge: np.ndarray, answer: np.ndarray) -> np.ndarray:
    return np.max(x @ dodge.T, 1) - np.max(x @ answer.T, 1)


class DodgeScorer:
    def __init__(self, embedder, path: Path = PROTOTYPES):
        self.embedder = embedder
        self.meta = json.loads(path.with_suffix(".json").read_text())
        z = np.load(path.with_suffix(".npz"))
        self.dodge, self.answer = z["dodge"], z["answer"]
        if self.meta["model"] != embedder.name:
            raise ValueError(f"dodge prototypes were built with {self.meta['model']}, not {embedder.name}")

    def badge(self, text: str, answered: bool = False) -> list[dict]:
        """answered: the reply has an accepted answer (format_status ok), so a look-first phrase is required."""
        if len(text) < 20 or (answered and not LOOK_FIRST.search(text)):
            return []
        x = self.embedder.embed([DODGE_PREFIX + text])
        score = float(_score(x, self.dodge, self.answer)[0])
        t = self.meta["thresholds"]
        if score <= t["medium"]:            # strictly above the cutoff (the budget's share of others reach it)
            return []
        variant = int(np.argmax(x @ self.dodge.T))
        return [{"badge": "WANTS TO EXPLORE", "confidence": "high" if score > t["high"] else "medium",
                 "score": round(score, 3), "variant": variant,
                 "variant_example": self.meta["variants"][variant]["example"][:120], "sentence": text[:200]}]


def build_dodge_prototypes(sources: list[tuple[list[dict], Path]], embedder, out: Path = PROTOTYPES,
                           k_dodge: int = K_DODGE, k_answer: int = K_ANSWER) -> dict:
    """Prototypes from the thinking-off runs of the given (rows, runs_dir) sources: dodges (is_dodge) against
    the prose of accepted answers (every level, agents' final replies included), leaving out answer prose
    with a look-first phrase (some answers open with "let me look..." and would teach the answer side dodge
    language); UNPARSABLE replies are
    negatives for calibration only. Thresholds on the score, from a false-alarm budget: medium where 5% of
    the negatives reach, high where 2% do; the share of dodges caught follows (recorded). Validation: prototypes built from the first source alone, scored on the
    other sources (held out). Writes out.npz and out.json (with a representative reply per dodge variant);
    returns the json."""
    pos, ans, unp = [], [], []
    for k, (rows, runs_dir) in enumerate(sources):
        for r in rows:
            if r.get("thinking") is not False:
                continue
            d = runs_dir / r["run_id"]
            if is_dodge(r):
                pos.append((k, dodge_text(r, d)))
            elif r.get("format_status") == "bad_format":
                unp.append((k, dodge_text(r, d)))
            elif r.get("format_status") == "ok":
                text = final_prose(r, d)
                if len(text) >= 20 and not LOOK_FIRST.search(text):    # look-first talk is dodge language
                    ans.append((k, text[:2000]))
    emb = lambda xs: np.asarray(embedder.embed([DODGE_PREFIX + t for _, t in xs])) if xs else None
    P, A = emb(pos), emb(ans)
    U = emb(unp) if unp else np.zeros((0, P.shape[1]), dtype=P.dtype)
    kp, ka, ku = (np.array([k for k, _ in xs]) for xs in (pos, ans, unp))
    # held-out check: prototypes from source 0, scored on sources 1..n
    D0, A0 = _kmeans(P[kp == 0], k_dodge), _kmeans(A[ka == 0], k_answer)
    held_p = _score(P[kp > 0], D0, A0)
    held_n = _score(np.concatenate([A[ka > 0], U[ku > 0]]), D0, A0)
    auc = float(np.mean(held_p[:, None] > held_n[None, :])) if len(held_p) and len(held_n) else None
    # the prototypes in use: all sources
    D, An = _kmeans(P, k_dodge), _kmeans(A, k_answer)
    sp, sn = _score(P, D, An), _score(np.concatenate([A, U]), D, An)
    medium = float(np.quantile(sn, 0.95))     # thresholds from a false-alarm budget: 5% / 2% of the others
    high = float(np.quantile(sn, 0.98))
    near = np.argmax(P @ D.T, 1)
    variants = []
    for j in range(len(D)):
        idx = np.where(near == j)[0]
        rep = idx[np.argmax(P[idx] @ D[j])] if len(idx) else None
        variants.append({"replies": int(len(idx)), "example": pos[rep][1][:300] if rep is not None else ""})
    rate = lambda a, t: round(float(np.mean(a > t)), 3)
    meta = {"model": embedder.name, "prefix": DODGE_PREFIX, "k_dodge": len(D), "k_answer": len(An),
            "dodges": int(len(P)), "answers": int(len(A)), "unparsable": int(len(U)),
            "thresholds": {"medium": round(medium, 4), "high": round(high, 4)},
            "at_medium": {"dodges_caught": rate(sp, medium), "others_over": rate(sn, medium)},
            "at_high": {"dodges_caught": rate(sp, high), "others_over": rate(sn, high)},
            "held_out": {"dodges": int(len(held_p)), "others": int(len(held_n)),
                         "auc": None if auc is None else round(auc, 3)},
            "variants": variants}
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez(out.with_suffix(".npz"), dodge=D.astype(np.float32), answer=An.astype(np.float32))
    out.with_suffix(".json").write_text(json.dumps(meta, indent=1) + "\n")
    return meta


def compute(rows: list[dict], runs_dir: Path, embedder) -> list[dict]:
    """One record per run: exact badges, intent badges of its final prose, and WANTS TO EXPLORE from the
    dodge prototypes (when they exist for this embedder)."""
    scorer = IntentScorer(embedder)
    try:
        dodge = DodgeScorer(embedder)
    except (FileNotFoundError, ValueError) as e:
        import sys
        print(f"badges: no WANTS TO EXPLORE ({e})", file=sys.stderr)
        dodge = None
    out = []
    for r in rows:
        d = runs_dir / r["run_id"]
        prose = final_prose(r, d)
        intent = scorer.badges(prose) if len(prose) >= 20 else []
        if dodge:
            intent += dodge.badge(dodge_text(r, d), answered=r.get("format_status") == "ok")
        out.append({"run_id": r["run_id"], "exact": exact_badges(r, d), "intent": intent})
    return out
