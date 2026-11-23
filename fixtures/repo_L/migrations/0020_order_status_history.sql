CREATE TABLE order_status_history (
    order_id TEXT NOT NULL REFERENCES orders (id),
    status TEXT NOT NULL,
    at TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'system'
);

CREATE INDEX order_status_history_order ON order_status_history (order_id);
