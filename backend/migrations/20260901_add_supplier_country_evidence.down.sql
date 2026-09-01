DROP INDEX IF EXISTS ix_suppliers_country_status;

ALTER TABLE suppliers
    DROP COLUMN IF EXISTS country_evidence,
    DROP COLUMN IF EXISTS country_status;
