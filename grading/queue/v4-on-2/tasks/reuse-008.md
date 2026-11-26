A partner noticed full card numbers in our webhook deliveries for payment events. Before a delivery
is queued, a `card_number` field in the event data must be masked the same way the shop masks card
numbers everywhere else it stores or shows them. Other fields go out unchanged.
