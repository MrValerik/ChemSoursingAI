ALTER TABLE search_runs
    ADD COLUMN IF NOT EXISTS parent_search_run_id INTEGER
    REFERENCES search_runs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_search_runs_parent_search_run_id
    ON search_runs(parent_search_run_id);

ALTER TABLE search_runs
    ADD COLUMN IF NOT EXISTS replay_mode VARCHAR(32);

CREATE INDEX IF NOT EXISTS ix_search_runs_replay_mode
    ON search_runs(replay_mode);
