CREATE TABLE IF NOT EXISTS network_records (
    id              BIGSERIAL PRIMARY KEY,
    trace_id        TEXT,
    method          TEXT,
    url             TEXT,
    status          INTEGER,
    request_body    TEXT,
    response_body   TEXT,
    duration_ms     INTEGER,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_network_records_trace_id ON network_records(trace_id);
CREATE INDEX IF NOT EXISTS idx_network_records_status ON network_records(status);
CREATE INDEX IF NOT EXISTS idx_network_records_created_at ON network_records(created_at);