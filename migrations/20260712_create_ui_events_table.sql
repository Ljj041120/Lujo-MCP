CREATE TABLE IF NOT EXISTS ui_events (
    id          BIGSERIAL PRIMARY KEY,
    trace_id    TEXT,
    action      TEXT,
    selector    TEXT,
    timestamp   DOUBLE PRECISION,
    metadata    JSONB
);
CREATE INDEX IF NOT EXISTS idx_ui_events_trace_id ON ui_events(trace_id);
CREATE INDEX IF NOT EXISTS idx_ui_events_action ON ui_events(action);
CREATE INDEX IF NOT EXISTS idx_ui_events_timestamp ON ui_events(timestamp);