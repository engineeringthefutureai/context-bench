Checkout needs to hold stock for an order. Add `Stock.reserve(sku, qty)` that lowers the
available quantity of `sku` by `qty`. If there isn't enough stock, nothing changes and the
request fails the same way other business-rule failures in the shop do, so that the API answers
with 409 and the error body includes the requested and available quantities as `requested` and
`available`.
