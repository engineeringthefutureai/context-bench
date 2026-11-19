"""cbench run (configs + sweep) and cbench report, with a fake model client."""
import httpx

from context_bench import results
from test_pieces5_8 import FakeClient, _experiment

REPLY = '<file path="shopkit/utils/units.py">\ndef parse_weight(text):\n    return 0\n</file>'


def test_configs_expand_and_filter(tmp_path):
    exp = _experiment(tmp_path, FakeClient(REPLY))
    rcs = exp.configs()
    l0 = [rc for rc in rcs if rc.level == "L0"]
    assert [rc.task_id for rc in l0] == ["control-001"]          # categories/tiers filter
    assert sum(rc.level == "L1" and rc.prompt_arm == "base" for rc in rcs) == len(exp.tasks)
    assert {rc.level for rc in exp.configs(["L1", "L3"])} == {"L1", "L3"}
    assert len({rc.run_id for rc in rcs}) == len(rcs)            # no duplicate configs


def test_sweep_resumes_skips_unimplemented_and_retries_server_errors(tmp_path):
    client = FakeClient(REPLY)
    exp = _experiment(tmp_path, client)
    exp.sweep(["L0", "L4"])                                       # L4 isn't implemented yet: skipped
    rows = results.load(exp.results_path)
    assert [(r["task_id"], r["level"]) for r in rows] == [("control-001", "L0")]
    exp.sweep(["L0"])                                             # already done: no new call
    assert len(client.calls) == 1

    def boom(messages, **kw):
        raise httpx.ReadTimeout("timed out")
    client.chat = boom
    exp.sweep(["L1"])                                             # server errors aren't recorded
    assert len(results.load(exp.results_path)) == 1


def test_report(tmp_path):
    exp = _experiment(tmp_path, FakeClient(REPLY))
    exp.sweep(["L0", "L1"])
    text = results.report(exp.results_path)
    assert "| control-001 (1) | ❌ 1/13 |" in text and "## Per configuration" in text
    assert "| L1 | 6 |" in text and "| L1+hint | 6 |" in text          # --level L1 includes the hint arm
    assert "helper not in context" in text
    assert results.report(tmp_path / "missing.jsonl").startswith("No results")
