ALTER TABLE communication_test_runs
    ADD COLUMN IF NOT EXISTS recipient_key VARCHAR(64),
    ADD COLUMN IF NOT EXISTS recipient_ciphertext TEXT;

CREATE INDEX IF NOT EXISTS ix_communication_test_runs_recipient_key
    ON communication_test_runs (recipient_key);
