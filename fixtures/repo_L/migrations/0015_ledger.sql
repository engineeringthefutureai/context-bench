-- Double-entry ledger; every journal entry balances (checked in code, see accounting.journal).
CREATE TABLE accounts (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('asset', 'liability', 'equity', 'revenue', 'expense'))
);

CREATE TABLE journal_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    posted_at TEXT NOT NULL,
    memo TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    source_ref TEXT
);

CREATE TABLE journal_lines (
    entry_id INTEGER NOT NULL REFERENCES journal_entries (id) ON DELETE CASCADE,
    account_code TEXT NOT NULL REFERENCES accounts (code),
    debit_cents INTEGER NOT NULL DEFAULT 0,
    credit_cents INTEGER NOT NULL DEFAULT 0,
    CHECK (debit_cents >= 0 AND credit_cents >= 0)
);

CREATE INDEX journal_lines_account ON journal_lines (account_code);
