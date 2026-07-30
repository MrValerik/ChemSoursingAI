ALTER TABLE agent_runs
    DROP COLUMN IF EXISTS contract_version;

DROP INDEX IF EXISTS ix_search_runs_correlation_id;

ALTER TABLE search_runs
    DROP COLUMN IF EXISTS graph_version;

ALTER TABLE search_runs
    DROP COLUMN IF EXISTS correlation_id;
