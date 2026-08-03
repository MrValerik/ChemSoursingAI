DROP INDEX IF EXISTS ix_communication_test_messages_sender_role;
DROP INDEX IF EXISTS ix_communication_test_messages_run_id;
DROP TABLE IF EXISTS communication_test_messages;

ALTER TABLE communication_test_runs DROP COLUMN subject;
ALTER TABLE communication_test_runs DROP COLUMN procurement_context;
