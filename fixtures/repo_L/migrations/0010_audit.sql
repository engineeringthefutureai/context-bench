CREATE TABLE audit_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    subject TEXT NOT NULL,
    details TEXT NOT NULL DEFAULT '{}',
    at TEXT NOT NULL
);

CREATE INDEX audit_subject ON audit_entries (subject);
