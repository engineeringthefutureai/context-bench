"""Badges: exact ones from the run record, intent ones by the per-sentence margin rule (fake embedder)."""
import json

import numpy as np

from context_bench import badges as B


class KeywordEmbedder:
    """One axis per intent label; a text points along the labels whose words it contains."""
    keys = {"DECLARES COMPLETION": "complete", "CLAIMS VERIFICATION": "pass", "WANTS TO EXPLORE": "explor",
            "STATES A PLAN": "plan", "NOTES MISSING CONTEXT": "context"}

    def embed(self, texts):
        out = []
        for t in texts:
            low = t.lower()
            v = np.array([1.0 if k in low else 0.0 for k in self.keys.values()] + [0.3])
            out.append(v / np.linalg.norm(v))
        return np.array(out)


def test_exact_badges(tmp_path):
    (tmp_path / "tests.txt").write_text("E   SyntaxError: invalid syntax")
    (tmp_path / "transcript.jsonl").write_text(json.dumps({"role": "user", "content": "These files you changed don't parse:\nx"}))
    row = {"format_status": "no_code", "format_retry": True, "legacy_used": True, "error": "output hit max_output_tokens (32768)"}
    assert B.exact_badges(row, tmp_path) == ["NO CODE", "OUT OF TOKENS", "RETRIED: FORMAT", "RETRIED: SYNTAX",
                                             "SYNTAX ERROR IN PATCH", "USES DEAD CODE"]


def test_final_prose_strips_code_and_takes_the_last_l7_reply(tmp_path):
    (tmp_path / "response.txt").write_text("ignored")
    (tmp_path / "transcript.jsonl").write_text("\n".join(json.dumps(m) for m in [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "assistant", "content": "The change is complete. <file path=\"a.py\">x = 1</file>"}]))
    assert B.final_prose({"level": "L7"}, tmp_path) == "The change is complete."


def test_intent_badges_need_a_margin_over_the_runner_up(monkeypatch):
    monkeypatch.setattr(B, "INTENTS", {lab: [kw] for lab, kw in KeywordEmbedder.keys.items()})
    sc = B.IntentScorer(KeywordEmbedder())
    found = sc.badges("The change is complete. All tests pass and the plan is done.")
    labels = {b["badge"]: b["confidence"] for b in found}
    assert labels.get("DECLARES COMPLETION") == "high"          # one clear label for the sentence
    assert "STATES A PLAN" not in labels                         # tied with "pass" in its sentence: no badge


class ExploreEmbedder:
    """Axis 0: look-first words; axis 1: "creating/done" words; axis 2: everything. Named like a real embedder
    (the prototypes check it)."""
    name = "fake-embed"

    def embed(self, texts):
        out = []
        for t in texts:
            low = t.lower()
            v = np.array([sum(w in low for w in ("explore", "look", "check")),
                          sum(w in low for w in ("creating", "done", "added")), 0.5], dtype=float)
            out.append(v / np.linalg.norm(v))
        return np.array(out)


def _run(tmp_path, rid, text, **row):
    d = tmp_path / rid
    d.mkdir()
    (d / "response.txt").write_text(text)
    return dict({"run_id": rid, "level": "L5", "thinking": False, "label": "broken"}, **row)


def test_dodge_prototypes_are_built_calibrated_and_used(tmp_path):
    dodge = ["Let me explore the repository first.", "I'll look at the relevant files and check the helpers."]
    answer = ["Creating the module now, the field is added.", "Done: the change is added to the model."]
    rows = []
    for src in range(2):
        rows.append([_run(tmp_path, f"d{src}{i}", dodge[i % 2], format_status="no_code") for i in range(6)]
                    + [_run(tmp_path, f"a{src}{i}", answer[i % 2] + ' <file path="a.py">x = 1</file>',
                            format_status="ok", label="ok") for i in range(6)])
    out = tmp_path / "grading" / "protos"
    meta = B.build_dodge_prototypes([(rows[0], tmp_path), (rows[1], tmp_path)], ExploreEmbedder(), out,
                                    k_dodge=2, k_answer=2)
    assert meta["dodges"] == 12 and meta["held_out"]["dodges"] == 6 and meta["held_out"]["auc"] == 1.0
    assert meta["at_medium"]["others_over"] == 0.0 and len(meta["variants"]) == 2
    scorer = B.DodgeScorer(ExploreEmbedder(), out)
    hit = scorer.badge("Let me explore the code and check the helpers first.")
    assert hit and hit[0]["badge"] == "WANTS TO EXPLORE" and "variant_example" in hit[0]
    assert scorer.badge("Creating the file now, done.") == []
    # an answered reply needs a look-first phrase even when it scores like a dodge
    assert scorer.badge("Exploring done? Check.", answered=True) == []


def test_dodge_prototypes_refuse_another_embedder(tmp_path):
    out = tmp_path / "c"
    np.savez(out.with_suffix(".npz"), dodge=np.eye(3)[:1], answer=np.eye(3)[1:2])
    out.with_suffix(".json").write_text(json.dumps({"model": "other", "thresholds": {"medium": 0.1, "high": 0.2}}))
    try:
        B.DodgeScorer(ExploreEmbedder(), out)
        assert False, "should refuse"
    except ValueError:
        pass
