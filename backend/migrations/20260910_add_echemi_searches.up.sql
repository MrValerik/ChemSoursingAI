CREATE TABLE IF NOT EXISTS echemi_searches (
    id SERIAL PRIMARY KEY,
    author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    query VARCHAR(200) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    message TEXT,
    results JSON NOT NULL DEFAULT '[]',
    diagnostics JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_echemi_searches_status ON echemi_searches(status);
