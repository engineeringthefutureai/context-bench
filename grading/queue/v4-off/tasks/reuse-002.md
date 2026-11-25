The storefront needs stable product page addresses. Add a read-only `url_path` property to
`Product` that returns "/products/<slug>", where the slug is made from the product title using
the same URL slug rules the shop already uses for titles.
