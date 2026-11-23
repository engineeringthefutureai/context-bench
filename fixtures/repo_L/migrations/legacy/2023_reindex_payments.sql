-- slow payment lookups, see INC-1874
DROP INDEX IF EXISTS payments_order;
CREATE INDEX payments_order ON payments (order_id);
CREATE INDEX payments_provider_ref ON payments (provider, provider_ref);
