from shopkit.warehouse.labels import pallet_label


def test_label_uses_finance_item_numbers():
    assert pallet_label("P-7", [("MUG-1", 3), ("tea-set-12", 1), ("GIFTCARD", 2)]) == \
        "PALLET P-7\nMUG000000001 x3\nTEASET000012 x1\nGIFTCARD0000 x2"
