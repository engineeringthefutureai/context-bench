-- Totals are stored at checkout so later price changes don't rewrite history.
ALTER TABLE orders ADD COLUMN subtotal_cents INTEGER NOT NULL DEFAULT 0;
ALTER TABLE orders ADD COLUMN discount_cents INTEGER NOT NULL DEFAULT 0;
ALTER TABLE orders ADD COLUMN shipping_cents INTEGER NOT NULL DEFAULT 0;
ALTER TABLE orders ADD COLUMN tax_cents INTEGER NOT NULL DEFAULT 0;
ALTER TABLE orders ADD COLUMN total_cents INTEGER NOT NULL DEFAULT 0;
