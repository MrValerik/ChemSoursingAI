DROP INDEX IF EXISTS ix_supplier_documents_text_status;
DROP INDEX IF EXISTS ix_supplier_documents_kind;
DROP INDEX IF EXISTS ix_supplier_documents_sha256;
DROP INDEX IF EXISTS ix_supplier_documents_supplier_id;
DROP INDEX IF EXISTS ix_supplier_documents_communication_id;
DROP INDEX IF EXISTS ix_supplier_documents_rfq_id;

DROP TABLE IF EXISTS supplier_documents;
