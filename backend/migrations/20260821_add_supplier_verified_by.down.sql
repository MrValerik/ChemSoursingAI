DROP INDEX IF EXISTS ix_suppliers_verified_by_id;

ALTER TABLE suppliers
    DROP COLUMN IF EXISTS verified_by_id;
