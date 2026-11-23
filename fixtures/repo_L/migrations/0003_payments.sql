CREATE TABLE payments (
    id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES orders (id),
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_ref TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE INDEX payments_order ON payments (order_id);
