ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS raw_output_payload JSONB;

ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS parsed_output_payload JSONB;

ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS validation_output_payload JSONB;

ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS policy_output_payload JSONB;
