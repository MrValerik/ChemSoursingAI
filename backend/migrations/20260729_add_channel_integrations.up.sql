CREATE TABLE IF NOT EXISTS integration_settings (
    id SERIAL PRIMARY KEY,
    channel VARCHAR(32) NOT NULL UNIQUE,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    encrypted_config TEXT NOT NULL,
    updated_by_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_integration_settings_channel
    ON integration_settings (channel);
CREATE INDEX IF NOT EXISTS ix_integration_settings_updated_by_id
    ON integration_settings (updated_by_id);

CREATE TABLE IF NOT EXISTS communication_test_runs (
    id SERIAL PRIMARY KEY,
    actor_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    channel VARCHAR(32) NOT NULL,
    recipient_masked VARCHAR(320) NOT NULL,
    customer_message TEXT NOT NULL,
    additional_instructions TEXT,
    generated_reply TEXT,
    model VARCHAR(255),
    reply_language VARCHAR(8) NOT NULL DEFAULT 'ru',
    delivery_mode VARCHAR(16) NOT NULL DEFAULT 'preview',
    status VARCHAR(32) NOT NULL,
    provider_message_id VARCHAR(255),
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_communication_test_runs_actor_id
    ON communication_test_runs (actor_id);
CREATE INDEX IF NOT EXISTS ix_communication_test_runs_channel
    ON communication_test_runs (channel);
CREATE INDEX IF NOT EXISTS ix_communication_test_runs_status
    ON communication_test_runs (status);
