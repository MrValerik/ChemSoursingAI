CREATE TABLE IF NOT EXISTS rfq_supplier_links (
    id SERIAL PRIMARY KEY,
    rfq_id INTEGER NOT NULL REFERENCES rfqs(id) ON DELETE CASCADE,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    search_run_id INTEGER REFERENCES search_runs(id) ON DELETE SET NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'candidate',
    source_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_rfq_supplier_link
    ON rfq_supplier_links (rfq_id, supplier_id);
CREATE INDEX IF NOT EXISTS ix_rfq_supplier_links_rfq_id
    ON rfq_supplier_links (rfq_id);
CREATE INDEX IF NOT EXISTS ix_rfq_supplier_links_supplier_id
    ON rfq_supplier_links (supplier_id);
CREATE INDEX IF NOT EXISTS ix_rfq_supplier_links_search_run_id
    ON rfq_supplier_links (search_run_id);
CREATE INDEX IF NOT EXISTS ix_rfq_supplier_links_status
    ON rfq_supplier_links (status);
