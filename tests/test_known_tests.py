"""The repo's-own-tests check by test id: a patch that breaks one module's import loses every test of that
module (not "0 of 1", as when pytest stopped at the collection error), the other modules still run, and
the lost tests are named in known_tests_broken."""
from pathlib import Path

from context_bench.apply import make_sandbox
from context_bench.evaluate.hidden_tests import known_tests_broken, repo_test_ids

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "tasks" / "_shared"


def test_import_error_loses_only_that_modules_tests(tmp_path):
    clean = make_sandbox(ROOT / "fixtures" / "repo_M", tmp_path / "clean")
    known, collected, _ = repo_test_ids(clean, SHARED)
    assert known and known == collected                     # the fixture's own tests all pass
    box = make_sandbox(ROOT / "fixtures" / "repo_M", tmp_path / "broken")
    returns = box / "shopkit" / "orders" / "returns.py"
    returns.write_text(returns.read_text().replace("def open_return(", "def open_return_renamed("))
    passed, _, out = repo_test_ids(box, SHARED)
    lost = known_tests_broken(known, passed)
    assert lost and all(t.startswith("tests/test_returns.py::") for t in lost), lost
    assert passed == known - set(lost)                       # every other module still ran and passed
    assert "error" in out.lower()


def test_test_ids_match_across_layouts(tmp_path):
    box = make_sandbox(ROOT / "fixtures" / "repo_M", tmp_path / "m")
    known, _, _ = repo_test_ids(box, SHARED)
    assert "tests/test_shipping.py::test_packing_slip" in known
    assert any(t.startswith("tests/test_CustomerUtils.py::CustomerUtilsTest::") for t in known)   # unittest classes
