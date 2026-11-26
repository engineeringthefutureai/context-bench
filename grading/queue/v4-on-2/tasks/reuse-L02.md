The new pallet scanner in the warehouse reads the finance system's item numbers, not our SKUs.
Add `pallet_label(pallet_id, items)` in a new module `shopkit/warehouse/labels.py`, where `items`
is a list of (sku, qty). It returns the label text: the first line `PALLET <pallet_id>`, then one
line per item, `<item number> x<qty>`, in the order given, where the item number is the one the
finance system knows that product by.
