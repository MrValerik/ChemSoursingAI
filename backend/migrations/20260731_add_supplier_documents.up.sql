CREATE TABLE IF NOT EXISTS supplier_documents (
    id SERIAL PRIMARY KEY,
    rfq_id INTEGER REFERENCES rfqs(id) ON DELETE SET NULL,
    communication_id INTEGER REFERENCES communications(id) ON DELETE SET NULL,
    supplier_id INTEGER REFERENCES suppliers(id) ON DELETE SET NULL,
    filename VARCHAR(500) NOT NULL,
    content_type VARCHAR(255) NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 VARCHAR(64) NOT NULL,
    storage_path VARCHAR(500) NOT NULL,
    kind VARCHAR(32) NOT NULL DEFAULT 'other',
    text_status VARCHAR(32) NOT NULL DEFAULT 'stored',
    text_content TEXT,
    page_count INTEGER,
    extraction_error TEXT,
    extracted_at TIMESTAMPTZ,
    verification JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_supplier_documents_rfq_id
    ON supplier_documents (rfq_id);

CREATE INDEX IF NOT EXISTS ix_supplier_documents_communication_id
    ON supplier_documents (communication_id);

CREATE INDEX IF NOT EXISTS ix_supplier_documents_supplier_id
    ON supplier_documents (supplier_id);

CREATE INDEX IF NOT EXISTS ix_supplier_documents_sha256
    ON supplier_documents (sha256);

CREATE INDEX IF NOT EXISTS ix_supplier_documents_kind
    ON supplier_documents (kind);

CREATE INDEX IF NOT EXISTS ix_supplier_documents_text_status
    ON supplier_documents (text_status);
