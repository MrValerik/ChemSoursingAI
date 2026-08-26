DROP INDEX IF EXISTS ix_intermediaries_source_rfq_id;
DROP INDEX IF EXISTS ix_intermediaries_added_by_id;

ALTER TABLE intermediaries
    DROP COLUMN IF EXISTS deactivated_at,
    DROP COLUMN IF EXISTS deactivated_by_id,
    DROP COLUMN IF EXISTS source_rfq_id,
    DROP COLUMN IF EXISTS source_url,
    DROP COLUMN IF EXISTS reason,
    DROP COLUMN IF EXISTS added_by_id;
