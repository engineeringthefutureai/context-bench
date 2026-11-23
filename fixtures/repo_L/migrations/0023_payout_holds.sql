-- Payout holds (INC-2291): money for orders still inside the return window is held back.
CREATE TABLE payout_holds (
    seller_id TEXT NOT NULL REFERENCES sellers (id),
    order_id TEXT NOT NULL REFERENCES orders (id),
    amount_cents INTEGER NOT NULL,
    release_after TEXT NOT NULL,
    released INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (seller_id, order_id)
);
