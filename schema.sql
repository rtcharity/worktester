CREATE TABLE IF NOT EXISTS candidates (
    token       TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    doc_url     TEXT NOT NULL,
    started_at  TEXT
);
