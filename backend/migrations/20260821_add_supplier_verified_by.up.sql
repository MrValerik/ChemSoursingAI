ALTER TABLE suppliers
    ADD COLUMN IF NOT EXISTS verified_by_id INTEGER
    REFERENCES users(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_suppliers_verified_by_id
    ON suppliers (verified_by_id);
