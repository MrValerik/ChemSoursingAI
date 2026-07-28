ALTER TABLE search_runs
    ADD COLUMN rfq_id INTEGER REFERENCES rfqs(id) ON DELETE SET NULL;

CREATE INDEX ix_search_runs_rfq_id ON search_runs (rfq_id);
