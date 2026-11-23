-- Marketplace: third-party sellers and their payouts.
CREATE TABLE sellers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    commission_bp INTEGER NOT NULL DEFAULT 1500,
    payout_currency TEXT NOT NULL DEFAULT 'USD',
    status TEXT NOT NULL DEFAULT 'active'
);

ALTER TABLE products ADD COLUMN seller_id TEXT REFERENCES sellers (id);

CREATE TABLE payouts (
    id TEXT PRIMARY KEY,
    seller_id TEXT NOT NULL REFERENCES sellers (id),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    gross_cents INTEGER NOT NULL,
    commission_cents INTEGER NOT NULL,
    adjustments_cents INTEGER NOT NULL DEFAULT 0,
    net_cents INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
);
