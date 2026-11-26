Checkout should only offer express delivery when the warehouse will really send the order by
express. Add `express_available(skus, sites, hazmat_skus)` in a new module
`shopkit/shipping/options.py`: `skus` are the cart's SKUs, `sites` the warehouse sites that could
ship it (warehouse.sites.Site), `hazmat_skus` the SKUs classed as dangerous goods. Return True when
the warehouse would put an order with these SKUs into an express wave, False otherwise.
