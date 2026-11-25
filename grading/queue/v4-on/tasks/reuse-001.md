Finance wants a spreadsheet of orders. Add `export_orders_csv(orders) -> str` next to the existing
JSON export. One row per order line, with the header `order_id,product_code,quantity,line_total`.
Product codes must be cleaned up the same way they are everywhere else in the shop, and line totals
must look exactly like they do on customer invoices. Use the standard csv module.
