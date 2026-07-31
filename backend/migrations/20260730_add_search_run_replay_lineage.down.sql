DROP INDEX IF EXISTS ix_search_runs_replay_mode;

ALTER TABLE search_runs
    DROP COLUMN IF EXISTS replay_mode;

DROP INDEX IF EXISTS ix_search_runs_parent_search_run_id;

ALTER TABLE search_runs
    DROP COLUMN IF EXISTS parent_search_run_id;
