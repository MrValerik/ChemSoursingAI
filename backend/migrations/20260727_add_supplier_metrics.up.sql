ALTER TABLE suppliers
    ADD COLUMN qualification_status VARCHAR(32) NOT NULL DEFAULT 'candidate',
    ADD COLUMN evidence_score INTEGER,
    ADD COLUMN last_checked_at TIMESTAMPTZ;

CREATE INDEX ix_suppliers_qualification_status
    ON suppliers (qualification_status);
