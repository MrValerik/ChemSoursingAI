DROP INDEX IF EXISTS ix_search_runs_rfq_id;

ALTER TABLE search_runs DROP COLUMN rfq_id;
