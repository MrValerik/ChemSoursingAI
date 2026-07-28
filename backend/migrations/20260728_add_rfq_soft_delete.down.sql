DROP INDEX IF EXISTS ix_rfqs_deleted_by_id;
DROP INDEX IF EXISTS ix_rfqs_deleted_at;

ALTER TABLE rfqs DROP COLUMN IF EXISTS deleted_by_id;
ALTER TABLE rfqs DROP COLUMN IF EXISTS deleted_at;
