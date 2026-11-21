from shopkit.api.pagination import paginate


def test_paginate():
    page = paginate(list(range(120)), page=3, per_page=50)
    assert page["items"] == list(range(100, 120))
    assert (page["total"], page["pages"], page["per_page"]) == (120, 3, 50)


def test_paginate_edges():
    assert paginate([], 1)["pages"] == 1
    assert paginate(list(range(5)), page=0, per_page=2)["page"] == 1
    assert paginate(list(range(5)), page=9, per_page=2)["items"] == []
    assert paginate(list(range(500)), per_page=1000)["per_page"] == 200
