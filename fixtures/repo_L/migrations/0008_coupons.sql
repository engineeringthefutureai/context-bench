CREATE TABLE coupons (
    code TEXT PRIMARY KEY,
    percent_off INTEGER CHECK (percent_off BETWEEN 0 AND 100),
    amount_off_cents INTEGER,
    starts_at TEXT,
    ends_at TEXT,
    max_uses INTEGER,
    uses INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE coupon_redemptions (
    code TEXT NOT NULL REFERENCES coupons (code),
    order_id TEXT NOT NULL REFERENCES orders (id),
    redeemed_at TEXT NOT NULL,
    PRIMARY KEY (code, order_id)
);
