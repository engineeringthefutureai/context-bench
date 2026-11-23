CREATE TABLE subscriptions (
    id TEXT PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES customers (id),
    plan TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    interval_days INTEGER NOT NULL,
    unit_cents INTEGER NOT NULL,
    current_period_start TEXT NOT NULL,
    current_period_end TEXT NOT NULL,
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE subscription_invoices (
    subscription_id TEXT NOT NULL REFERENCES subscriptions (id),
    period_start TEXT NOT NULL,
    amount_cents INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'open',
    PRIMARY KEY (subscription_id, period_start)
);
