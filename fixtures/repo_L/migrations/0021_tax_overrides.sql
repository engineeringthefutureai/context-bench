CREATE TABLE tax_overrides (
    country TEXT NOT NULL,
    region TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL DEFAULT '',
    rate_bp INTEGER NOT NULL,
    PRIMARY KEY (country, region, category)
);
