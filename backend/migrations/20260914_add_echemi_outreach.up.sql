CREATE TABLE IF NOT EXISTS echemi_outreach (
    id SERIAL PRIMARY KEY,
    rfq_id INTEGER NOT NULL REFERENCES rfqs(id) ON DELETE CASCADE,
    search_id INTEGER NOT NULL REFERENCES echemi_searches(id) ON DELETE CASCADE,
    author_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    product_url VARCHAR(1000) NOT NULL,
    seller_name VARCHAR(500) NOT NULL,
    encrypted_payload TEXT NOT NULL,
    attempts JSON NOT NULL DEFAULT '[]',
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    message TEXT,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_echemi_outreach_target UNIQUE (rfq_id, product_url)
);
CREATE INDEX IF NOT EXISTS ix_echemi_outreach_rfq_id ON echemi_outreach(rfq_id);
CREATE INDEX IF NOT EXISTS ix_echemi_outreach_status ON echemi_outreach(status);
