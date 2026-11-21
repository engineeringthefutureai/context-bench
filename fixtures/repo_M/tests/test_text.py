from shopkit.utils.text import normalize_sku, slug_for_title


def test_normalize_sku():
    assert normalize_sku("ab 12-x") == "AB-12-X"
    assert normalize_sku("tsh/red/42") == "TSHRED-42"


def test_slug_for_title():
    assert slug_for_title("Crème Brûlée Set (6 pcs)") == "creme-brulee-set-6-pcs"
    assert slug_for_title("  ") == ""
