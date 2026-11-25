Support agents want the order references in a customer's message turned into links. Add
`order_refs(text)` in a new module `shopkit/support/linking.py`. It returns the order references
mentioned in the text, in the order they first appear and without duplicates, each written the way
the shop writes that kind of reference everywhere else. Customers type them in all sorts of ways:
"so 2019 123", "SO-2019-000123", "ORD_1A2B3C4D5E6F". Anything that isn't a valid order reference is
left out.
