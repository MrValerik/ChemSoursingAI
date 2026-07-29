ALTER TABLE search_runs
    ADD COLUMN IF NOT EXISTS rfq_id INTEGER REFERENCES rfqs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_search_runs_rfq_id ON search_runs (rfq_id);
