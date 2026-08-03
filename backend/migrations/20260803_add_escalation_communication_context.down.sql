DROP INDEX IF EXISTS ix_escalations_manager_id;
DROP INDEX IF EXISTS ix_escalations_communication_id;

ALTER TABLE escalations DROP COLUMN IF EXISTS manager_id;
ALTER TABLE escalations DROP COLUMN IF EXISTS communication_id;
