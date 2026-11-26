Changed-mind returns will now carry a 12.5% restocking fee. Add `restocking_fee_cents(order)` to
`ReturnRequest`: 12.5% of the return's refund amount for changed-mind returns, 0 for every other
reason. It is a percentage of money, so round it exactly the way the shop rounds its other
percentage amounts such as tax and discounts.
