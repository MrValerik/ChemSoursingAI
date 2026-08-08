DROP INDEX IF EXISTS ix_suppliers_company_key;

ALTER TABLE suppliers DROP COLUMN IF EXISTS company_key;
