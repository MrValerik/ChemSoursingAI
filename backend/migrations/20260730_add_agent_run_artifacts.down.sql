ALTER TABLE agent_runs
    DROP COLUMN IF EXISTS policy_output_payload;

ALTER TABLE agent_runs
    DROP COLUMN IF EXISTS validation_output_payload;

ALTER TABLE agent_runs
    DROP COLUMN IF EXISTS parsed_output_payload;

ALTER TABLE agent_runs
    DROP COLUMN IF EXISTS raw_output_payload;
