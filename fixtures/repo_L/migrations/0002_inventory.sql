-- Stock per warehouse.
CREATE TABLE warehouses (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    country TEXT NOT NULL DEFAULT 'US'
);

CREATE TABLE stock_levels (
    warehouse_code TEXT NOT NULL REFERENCES warehouses (code),
    sku TEXT NOT NULL REFERENCES products (sku),
    on_hand INTEGER NOT NULL DEFAULT 0,
    reserved INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (warehouse_code, sku)
);
