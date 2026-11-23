CREATE TABLE price_lists (
    code TEXT PRIMARY KEY,
    currency TEXT NOT NULL,
    customer_tier TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE price_list_entries (
    price_list TEXT NOT NULL REFERENCES price_lists (code),
    sku TEXT NOT NULL REFERENCES products (sku),
    min_qty INTEGER NOT NULL DEFAULT 1,
    unit_cents INTEGER NOT NULL,
    PRIMARY KEY (price_list, sku, min_qty)
);
