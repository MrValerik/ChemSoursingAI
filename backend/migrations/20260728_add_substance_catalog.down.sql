DROP INDEX IF EXISTS ix_rfqs_substance_id;
ALTER TABLE rfqs DROP COLUMN IF EXISTS substance_id;

DROP INDEX IF EXISTS ix_substances_reviewed_by_id;
DROP INDEX IF EXISTS ix_substances_review_status;
DROP INDEX IF EXISTS ix_substances_cas;
DROP TABLE IF EXISTS substances;
