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
