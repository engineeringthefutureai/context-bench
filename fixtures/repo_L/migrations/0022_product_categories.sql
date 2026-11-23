ALTER TABLE products ADD COLUMN category TEXT NOT NULL DEFAULT '';
ALTER TABLE products ADD COLUMN tax_category TEXT NOT NULL DEFAULT 'standard';

CREATE INDEX products_category ON products (category);
