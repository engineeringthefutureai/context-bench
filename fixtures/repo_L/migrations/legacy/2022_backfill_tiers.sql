-- backfill customer tiers from the old CRM export (ticket OPS-118)
UPDATE customers SET tier = 'gold' WHERE id IN (SELECT customer_id FROM legacy_crm_gold);
UPDATE customers SET tier = 'silver' WHERE id IN (SELECT customer_id FROM legacy_crm_silver);
DROP TABLE legacy_crm_gold;
DROP TABLE legacy_crm_silver;
