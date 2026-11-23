-- One-off cleanup after the double-submit bug (March 2021). Run by hand once; kept for reference.
-- DO NOT RUN AGAIN.
DELETE FROM order_lines WHERE order_id IN (
    SELECT id FROM orders WHERE id LIKE 'DUP-%'
);
DELETE FROM orders WHERE id LIKE 'DUP-%';
