"""metrics.py: usage visible, dead-code share of context, dead code used by the patch."""
from pathlib import Path

from context_bench.levels import LEVELS
from context_bench.metrics import dead_modules, legacy_ctx_share, legacy_used, usage_visible
from context_bench.tasks import load_tasks
from context_bench.workspace import load_workspace

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ["shopkit/common/", "shopkit/utils/strings.py"]
M = load_workspace(ROOT / "fixtures" / "repo_M")
TASKS = load_tasks(ROOT / "tasks")


def test_usage_visible_needs_an_import_or_call_not_the_def():
    h = ("shopkit.money.format_money",)
    assert usage_visible(h, "def format_money(cents):\n    pass") is False
    assert usage_visible(h, "from shopkit.money import format_money") is True
    assert usage_visible(h, "    total = format_money(c)") is True
    assert usage_visible((), "anything") is None


def test_legacy_share_by_block_and_by_line():
    text = ("File: a.py\n```python\nx = 1\n```\n"
            "File: shopkit/common/db.py\n```python\ny = 2\n```\n"
            "shopkit/common/db.py:1: y = 2\n"
            "shopkit/utils/text.py\n  def slug_for_title(t)")
    share = legacy_ctx_share(text, LEGACY)
    assert 0.3 < share < 0.6
    assert legacy_ctx_share("File: a.py\n```python\nx = 1\n```", LEGACY) == 0.0
    assert legacy_ctx_share("", LEGACY) == 0.0


def test_legacy_share_per_level_on_repo_m():
    l3 = legacy_ctx_share(LEVELS["L3"](TASKS["reuse-001"], M).text, LEGACY)
    l1 = legacy_ctx_share(LEVELS["L1"](TASKS["reuse-001"], M).text, LEGACY)
    l6 = legacy_ctx_share(LEVELS["L6"](TASKS["reuse-001"], M).text, LEGACY)
    assert l1 == 0.0 and 0.05 < l3 < 0.3 and 0.0 < l6 < 0.3     # M's dead code is ~4-5k of ~40k tokens


def test_legacy_used_imports_only():
    mods = dead_modules(LEGACY, list(M.files))
    assert "shopkit.common.StringUtils" in mods and "shopkit.utils.strings" in mods and "shopkit.utils.text" not in mods
    assert legacy_used("+from shopkit.utils.strings import slugify\n", mods)
    assert legacy_used("+from shopkit.common import StringUtils\n", mods)
    assert not legacy_used("+from shopkit.utils.text import slug_for_title\n", mods)
    assert not legacy_used("-from shopkit.common import StringUtils\n", mods)      # removed, not added
