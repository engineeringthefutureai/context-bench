Marketing's CRM sync needs a lookup from email address to customer. Add
`by_email(customers) -> dict[str, Customer]` to the customers report module, keyed by the email
address trimmed and lowercased, and built the same way the shop builds its other lookup tables.
