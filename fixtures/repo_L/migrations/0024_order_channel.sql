-- requires: 0011
ALTER TABLE orders ADD COLUMN channel TEXT NOT NULL DEFAULT 'web';
CREATE INDEX orders_created ON orders (created_at);
