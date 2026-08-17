CREATE TABLE IF NOT EXISTS purchase_decisions (
    id SERIAL PRIMARY KEY,
    rfq_id INTEGER NOT NULL REFERENCES rfqs(id) ON DELETE CASCADE,
    quotation_id INTEGER NOT NULL REFERENCES quotations(id) ON DELETE RESTRICT,
    selected_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_purchase_decisions_rfq_id UNIQUE (rfq_id)
);

CREATE INDEX IF NOT EXISTS ix_purchase_decisions_rfq_id
    ON purchase_decisions (rfq_id);

CREATE INDEX IF NOT EXISTS ix_purchase_decisions_quotation_id
    ON purchase_decisions (quotation_id);
