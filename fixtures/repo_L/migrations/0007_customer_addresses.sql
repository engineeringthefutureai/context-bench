CREATE TABLE addresses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL REFERENCES customers (id),
    kind TEXT NOT NULL CHECK (kind IN ('billing', 'shipping')),
    line1 TEXT NOT NULL,
    line2 TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL,
    postcode TEXT NOT NULL,
    country TEXT NOT NULL
);

CREATE UNIQUE INDEX customers_email ON customers (email);
