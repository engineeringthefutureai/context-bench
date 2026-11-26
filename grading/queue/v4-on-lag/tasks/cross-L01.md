Customers can now leave a delivery note for the courier (for example "leave it with the neighbour
at no. 5"). Add `delivery_note: str = ""` to orders and make it work end to end: a new database
migration adds a `delivery_note` column to the orders table (text, empty by default); the order
repository saves the note when an order is added and loads it back; the order page shows it under a
"Delivery note" heading, only when there is one; and the order JSON in the API includes it as
`delivery_note`. Notes are at most 200 characters: storing a longer one raises ValueError.
