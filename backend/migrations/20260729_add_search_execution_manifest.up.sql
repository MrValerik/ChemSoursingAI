ALTER TABLE search_runs
    ADD COLUMN IF NOT EXISTS correlation_id VARCHAR(36);

UPDATE search_runs
SET correlation_id = '00000000-0000-0000-0000-' || LPAD(id::text, 12, '0')
WHERE correlation_id IS NULL;

ALTER TABLE search_runs
    ALTER COLUMN correlation_id SET NOT NULL;

ALTER TABLE search_runs
    ADD COLUMN IF NOT EXISTS graph_version VARCHAR(64)
    NOT NULL DEFAULT 'supplier-search.v1';

CREATE UNIQUE INDEX IF NOT EXISTS ix_search_runs_correlation_id
    ON search_runs (correlation_id);

ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS contract_version VARCHAR(64)
    NOT NULL DEFAULT 'v1';
