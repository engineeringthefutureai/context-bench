from shopkit.catalog.importer import import_products

CSV = """sku,title,price,active
ab 1,Blue Mug,$12.50,yes
AB-1,Duplicate,$1.00,yes
cd 2,,$3.00,yes
ef 3,Plate,$4.00,no
"""


def test_import_products_reports_bad_rows():
    report = import_products(CSV)
    assert [p.sku for p in report.created] == ["AB-1", "EF-3"]
    assert report.created[0].price_cents == 1250 and not report.created[1].active
    assert report.errors == ["row 3: duplicate sku AB-1", "row 4: missing title"]


def test_missing_columns():
    assert import_products("sku,title\nA,B\n").errors == ["missing columns: price"]
