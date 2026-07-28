DROP INDEX IF EXISTS ix_suppliers_qualification_status;

ALTER TABLE suppliers
    DROP COLUMN last_checked_at,
    DROP COLUMN evidence_score,
    DROP COLUMN qualification_status;
