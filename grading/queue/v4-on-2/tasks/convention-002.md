Stock transfers between warehouses must show up in the audit trail and reach notification
subscribers. When a transfer succeeds, announce it the same way the rest of the shop announces
domain events, with the SKU, quantity, source and destination, so the audit trail files it under
the SKU. `transfer()` keeps its signature; a failed transfer announces nothing.
