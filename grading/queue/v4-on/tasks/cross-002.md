Products now have a shipping weight. Add `weight_g: int = 0` (grams per unit) to `Product`. Add
`parcel_weight_g(parcel, products)` to the fulfillment module: the total weight of a parcel's
contents, where `products` maps SKU to product. The product representation the API returns must
include the weight as `weight_g`.
