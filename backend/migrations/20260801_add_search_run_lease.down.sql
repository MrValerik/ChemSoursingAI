DROP INDEX IF EXISTS ix_search_runs_lease_expires_at;

ALTER TABLE search_runs
    DROP COLUMN IF EXISTS lease_generation;

ALTER TABLE search_runs
    DROP COLUMN IF EXISTS lease_expires_at;

ALTER TABLE search_runs
    DROP COLUMN IF EXISTS lease_owner;
