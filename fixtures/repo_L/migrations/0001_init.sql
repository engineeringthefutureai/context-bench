-- Initial schema: catalog, customers, orders.
CREATE TABLE products (
    sku TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE customers (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE orders (
    id TEXT PRIMARY KEY,
    customer_id TEXT REFERENCES customers (id),
    currency TEXT NOT NULL DEFAULT 'USD',
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL
);

CREATE TABLE order_lines (
    order_id TEXT NOT NULL REFERENCES orders (id) ON DELETE CASCADE,
    line_no INTEGER NOT NULL,
    sku TEXT NOT NULL,
    qty INTEGER NOT NULL CHECK (qty > 0),
    unit_cents INTEGER NOT NULL,
    PRIMARY KEY (order_id, line_no)
);
