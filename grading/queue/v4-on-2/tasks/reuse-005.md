We're adding pay-by-bank-transfer. Customers type a payment reference into their banking app,
often copying it from a printed invoice or hearing it over the phone. Add
`transfer_reference() -> str` to the payments gateway module that generates a new reference: 12
characters in three dash-separated groups of four, in the same style as the shop's other
customer-facing codes.
