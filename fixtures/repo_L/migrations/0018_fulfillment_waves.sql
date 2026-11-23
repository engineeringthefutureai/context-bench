CREATE TABLE pick_waves (
    id TEXT PRIMARY KEY,
    warehouse_code TEXT NOT NULL REFERENCES warehouses (code),
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    released_at TEXT
);

CREATE TABLE wave_allocations (
    wave_id TEXT NOT NULL REFERENCES pick_waves (id),
    order_id TEXT NOT NULL REFERENCES orders (id),
    sku TEXT NOT NULL,
    qty INTEGER NOT NULL,
    bin TEXT,
    PRIMARY KEY (wave_id, order_id, sku)
);
