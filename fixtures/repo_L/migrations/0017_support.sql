CREATE TABLE tickets (
    id TEXT PRIMARY KEY,
    customer_id TEXT REFERENCES customers (id),
    order_id TEXT REFERENCES orders (id),
    subject TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    priority TEXT NOT NULL DEFAULT 'normal',
    opened_at TEXT NOT NULL,
    first_response_at TEXT,
    closed_at TEXT
);

CREATE TABLE ticket_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id TEXT NOT NULL REFERENCES tickets (id) ON DELETE CASCADE,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    internal INTEGER NOT NULL DEFAULT 0,
    at TEXT NOT NULL
);
