Shoppers can add a gift message to an order. Add `gift_message: str = ""` to `Order`. The message
must be printed on the packing slip of the order's first parcel only: carry it through
`split_into_parcels` so that `packing_slip(parcel, weight_g)` can print it. The order
representation the API returns must include it as `gift_message`. Customer invoices must not show
it.
