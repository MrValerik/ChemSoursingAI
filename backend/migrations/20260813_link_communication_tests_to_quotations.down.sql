DROP INDEX IF EXISTS ix_communication_test_runs_quotation_id;
DROP INDEX IF EXISTS ix_communication_test_runs_rfq_id;

ALTER TABLE communication_test_runs
    DROP COLUMN IF EXISTS quotation_id;

ALTER TABLE communication_test_runs
    DROP COLUMN IF EXISTS rfq_id;
