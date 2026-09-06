DROP TABLE IF EXISTS quotation_field_audits;

ALTER TABLE quotations
    DROP COLUMN IF EXISTS field_provenance;
