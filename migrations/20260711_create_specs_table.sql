CREATE TABLE IF NOT EXISTS specs (
    id          TEXT PRIMARY KEY,
    kind        TEXT,
    target      TEXT,
    expect      JSONB,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_specs_kind ON specs(kind);
CREATE INDEX IF NOT EXISTS idx_specs_target ON specs(target);