CREATE TABLE invoices (
    number TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders (id),
    issued_at TEXT NOT NULL,
    total_cents INTEGER NOT NULL,
    currency TEXT NOT NULL
);

CREATE TABLE invoice_sequences (
    prefix TEXT NOT NULL,
    year INTEGER NOT NULL,
    last_value INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (prefix, year)
);
