DROP INDEX IF EXISTS ix_quotations_source_communication_id;

ALTER TABLE quotations
    DROP COLUMN IF EXISTS source_communication_id;
