CREATE TABLE shipments (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders (id),
    carrier TEXT NOT NULL,
    tracking_number TEXT,
    status TEXT NOT NULL DEFAULT 'label_created',
    shipped_at TEXT
);

CREATE INDEX shipments_order ON shipments (order_id);
