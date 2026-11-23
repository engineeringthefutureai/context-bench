ALTER TABLE customers ADD COLUMN tier TEXT NOT NULL DEFAULT 'standard';

CREATE TABLE loyalty_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id TEXT NOT NULL REFERENCES customers (id),
    points INTEGER NOT NULL,
    reason TEXT NOT NULL,
    order_id TEXT REFERENCES orders (id),
    created_at TEXT NOT NULL
);
