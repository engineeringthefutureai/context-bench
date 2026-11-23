from shopkit.core.order_engine import wmsStatusLabel


def test_every_warehouse_code_has_a_label():
    codes = ["RCV", "ALC", "PCK", "PKD", "DSP", "SHT", "CNL", "HLD"]
    assert [wmsStatusLabel(c) for c in codes] == ["Received", "Allocated", "Picking", "Packed", "Dispatched",
                                                 "Short pick", "Cancelled", "On hold"]


def test_case_spaces_and_unknown():
    assert wmsStatusLabel(" dsp ") == "Dispatched"
    assert wmsStatusLabel("XYZ") == "Unknown" and wmsStatusLabel(None) == "Unknown"
