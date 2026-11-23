CREATE TABLE refunds (
    id TEXT PRIMARY KEY,
    payment_id TEXT NOT NULL REFERENCES payments (id),
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    reason TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
