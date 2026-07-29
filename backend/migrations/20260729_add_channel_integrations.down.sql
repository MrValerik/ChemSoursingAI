DROP INDEX IF EXISTS ix_communication_test_runs_status;
DROP INDEX IF EXISTS ix_communication_test_runs_channel;
DROP INDEX IF EXISTS ix_communication_test_runs_actor_id;
DROP TABLE IF EXISTS communication_test_runs;

DROP INDEX IF EXISTS ix_integration_settings_updated_by_id;
DROP INDEX IF EXISTS ix_integration_settings_channel;
DROP TABLE IF EXISTS integration_settings;
