Sellers want to see what their next payout will look like before finance runs the weekly batch.
Add `preview_payout(seller, earnings, adjustments, period_start, period_end, holds, rollover)` in a
new module `shopkit/payouts/preview.py`. It returns `(status, net_cents)`: the status and the net
amount this seller's payout would get for that period, exactly as the weekly payout would treat it.
A preview must not change anything: held amounts and balances carried between periods stay as they were.
