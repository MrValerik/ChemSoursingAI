DROP INDEX IF EXISTS ix_supplier_documents_manufacturer_decision;

ALTER TABLE supplier_documents
    DROP COLUMN IF EXISTS manufacturer_decided_at,
    DROP COLUMN IF EXISTS manufacturer_decided_by_id,
    DROP COLUMN IF EXISTS manufacturer_decision_reason,
    DROP COLUMN IF EXISTS manufacturer_decision;
