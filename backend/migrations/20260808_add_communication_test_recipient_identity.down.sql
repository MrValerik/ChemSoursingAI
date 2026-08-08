DROP INDEX IF EXISTS ix_communication_test_runs_recipient_key;

ALTER TABLE communication_test_runs
    DROP COLUMN IF EXISTS recipient_ciphertext,
    DROP COLUMN IF EXISTS recipient_key;
