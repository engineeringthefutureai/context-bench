CREATE TABLE fraud_checks (
    order_id TEXT PRIMARY KEY REFERENCES orders (id),
    score INTEGER NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('accept', 'review', 'reject')),
    reasons TEXT NOT NULL DEFAULT '[]',
    checked_at TEXT NOT NULL
);
