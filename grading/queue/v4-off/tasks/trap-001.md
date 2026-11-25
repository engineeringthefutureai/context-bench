Customers in the United States keep asking what "kg" means on their parcels. Add the parcel weight
to shipping labels: `label_text` gets an optional `weight_g` argument (the parcel's weight in
grams). When it is given, print a line `Weight: <weight>` right after the order/parcel line.
Labels for US addresses show pounds with two decimals (1500 g -> `3.31 lb`); every other
destination keeps the shop's standard kilogram display (1500 g -> `1.5 kg`). Use the shop's
standard weight formatting so labels match the packing slips.
