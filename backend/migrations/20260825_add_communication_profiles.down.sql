DROP TABLE IF EXISTS communication_policy_audits;

ALTER TABLE rfq_ai_settings DROP COLUMN IF EXISTS communication_profile_id;

DROP INDEX IF EXISTS ix_users_communication_profile_id;
ALTER TABLE users DROP COLUMN IF EXISTS communication_profile_id;

DROP TABLE IF EXISTS communication_profile_versions;
DROP TABLE IF EXISTS communication_profiles;
