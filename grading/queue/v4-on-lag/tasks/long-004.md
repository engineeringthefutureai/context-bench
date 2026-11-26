Shoppers want to see what a discount code would save them before they apply it. Add
`coupon_savings(items, products, code, coupons, engine, ship_to, weight_g, at)` in a new module
`shopkit/pricing/coupon_preview.py`. It returns the saving in cents that the code would give on this
cart, calculated exactly as the cart page calculates it, and 0 when the code wouldn't apply.
