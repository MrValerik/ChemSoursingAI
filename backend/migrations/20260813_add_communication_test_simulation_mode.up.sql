ALTER TABLE communication_test_runs
    ADD COLUMN IF NOT EXISTS simulation_mode VARCHAR(32) NOT NULL DEFAULT 'buyer_ai';
