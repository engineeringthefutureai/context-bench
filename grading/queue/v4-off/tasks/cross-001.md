Orders can now carry a percentage discount. Add `discount_percent: int = 0` (0 to 100) to orders.
The order total must reflect the discount; the discount amount is rounded down to whole cents.
Customer invoices must show the discount amount on its own line just before the total (only for
discounted orders; undiscounted invoices stay exactly as they are today). The warehouse JSON
export must include the discount as `discount_percent`.
