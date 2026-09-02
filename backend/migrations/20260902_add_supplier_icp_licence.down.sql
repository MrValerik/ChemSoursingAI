DROP INDEX IF EXISTS ix_suppliers_icp_licence;

ALTER TABLE suppliers
    DROP COLUMN IF EXISTS icp_licence;
