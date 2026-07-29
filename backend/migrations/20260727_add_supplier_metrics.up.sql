ALTER TABLE suppliers
    ADD COLUMN IF NOT EXISTS qualification_status VARCHAR(32) NOT NULL DEFAULT 'candidate',
    ADD COLUMN IF NOT EXISTS evidence_score INTEGER,
    ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_suppliers_qualification_status
    ON suppliers (qualification_status);
