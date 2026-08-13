ALTER TABLE communication_test_runs
    ADD COLUMN IF NOT EXISTS rfq_id INTEGER REFERENCES rfqs(id) ON DELETE SET NULL;

ALTER TABLE communication_test_runs
    ADD COLUMN IF NOT EXISTS quotation_id INTEGER REFERENCES quotations(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_communication_test_runs_rfq_id
    ON communication_test_runs (rfq_id);

CREATE UNIQUE INDEX IF NOT EXISTS ix_communication_test_runs_quotation_id
    ON communication_test_runs (quotation_id)
    WHERE quotation_id IS NOT NULL;
