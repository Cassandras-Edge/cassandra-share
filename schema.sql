-- cassandra-share schema (applied at startup)

CREATE TABLE IF NOT EXISTS shares (
  token         TEXT PRIMARY KEY,           -- 22-char base64url (128 bits)
  owner_email   TEXT NOT NULL,
  title         TEXT,                       -- optional human hint
  summary       TEXT,                       -- 2-3 line blurb shown in clipboard text
  body          TEXT NOT NULL,              -- sanitized markdown payload
  once          INTEGER NOT NULL DEFAULT 0, -- 1 = self-destruct after first fetch
  consumed_at   TEXT,                       -- set when once=1 and first fetch happens
  created_at    TEXT NOT NULL DEFAULT (datetime('now')),
  expires_at    TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shares_owner ON shares(owner_email);
CREATE INDEX IF NOT EXISTS idx_shares_expires ON shares(expires_at);
