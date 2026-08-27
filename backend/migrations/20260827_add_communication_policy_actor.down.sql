DROP INDEX IF EXISTS ix_communication_policy_audits_actor_id;

ALTER TABLE communication_policy_audits
    DROP COLUMN IF EXISTS actor_id;
