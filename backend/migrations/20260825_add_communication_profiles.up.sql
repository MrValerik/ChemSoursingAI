CREATE TABLE communication_profiles (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(64) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    system_instructions TEXT NOT NULL,
    required_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    version INTEGER NOT NULL DEFAULT 1,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    is_system BOOLEAN NOT NULL DEFAULT FALSE,
    max_input_chars INTEGER NOT NULL DEFAULT 12000,
    max_auto_replies INTEGER NOT NULL DEFAULT 12,
    max_duration_minutes INTEGER NOT NULL DEFAULT 10080,
    max_prompt_tokens INTEGER NOT NULL DEFAULT 60000,
    max_completion_tokens INTEGER NOT NULL DEFAULT 12000,
    max_estimated_cost_usd NUMERIC(12, 4) NOT NULL DEFAULT 10,
    updated_by VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_communication_profiles_slug ON communication_profiles (slug);
CREATE INDEX ix_communication_profiles_is_active ON communication_profiles (is_active);

CREATE TABLE communication_profile_versions (
    id SERIAL PRIMARY KEY,
    profile_id INTEGER NOT NULL REFERENCES communication_profiles(id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    system_instructions TEXT NOT NULL,
    required_fields JSONB NOT NULL DEFAULT '[]'::jsonb,
    max_input_chars INTEGER NOT NULL,
    max_auto_replies INTEGER NOT NULL,
    max_duration_minutes INTEGER NOT NULL,
    max_prompt_tokens INTEGER NOT NULL,
    max_completion_tokens INTEGER NOT NULL,
    max_estimated_cost_usd NUMERIC(12, 4) NOT NULL,
    changed_by VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (profile_id, version)
);

CREATE INDEX ix_communication_profile_versions_profile_id
    ON communication_profile_versions (profile_id);

ALTER TABLE users
    ADD COLUMN communication_profile_id INTEGER
    REFERENCES communication_profiles(id) ON DELETE SET NULL;
CREATE INDEX ix_users_communication_profile_id ON users (communication_profile_id);

ALTER TABLE rfq_ai_settings
    ADD COLUMN communication_profile_id INTEGER
    REFERENCES communication_profiles(id) ON DELETE SET NULL;

CREATE TABLE communication_policy_audits (
    id SERIAL PRIMARY KEY,
    event_key VARCHAR(255) NOT NULL UNIQUE,
    rfq_id INTEGER REFERENCES rfqs(id) ON DELETE SET NULL,
    manager_id INTEGER REFERENCES managers(id) ON DELETE SET NULL,
    communication_id INTEGER REFERENCES communications(id) ON DELETE SET NULL,
    test_run_id INTEGER REFERENCES communication_test_runs(id) ON DELETE SET NULL,
    profile_id INTEGER REFERENCES communication_profiles(id) ON DELETE SET NULL,
    profile_slug VARCHAR(64) NOT NULL,
    profile_name VARCHAR(255) NOT NULL,
    profile_version INTEGER NOT NULL,
    prompt_template_id INTEGER REFERENCES prompt_templates(id) ON DELETE SET NULL,
    prompt_version INTEGER,
    policy_route VARCHAR(32) NOT NULL DEFAULT 'pending',
    policy_category VARCHAR(64) NOT NULL DEFAULT 'unclear',
    policy_explanation TEXT NOT NULL DEFAULT '',
    policy_method VARCHAR(32) NOT NULL DEFAULT 'rule',
    input_chars INTEGER NOT NULL DEFAULT 0,
    automatic_replies_used INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds INTEGER NOT NULL DEFAULT 0,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd NUMERIC(12, 6) NOT NULL DEFAULT 0,
    reply_generated BOOLEAN NOT NULL DEFAULT FALSE,
    stop_reason VARCHAR(64),
    budget_snapshot JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_communication_policy_audits_rfq_id ON communication_policy_audits (rfq_id);
CREATE INDEX ix_communication_policy_audits_manager_id ON communication_policy_audits (manager_id);
CREATE INDEX ix_communication_policy_audits_communication_id ON communication_policy_audits (communication_id);
CREATE INDEX ix_communication_policy_audits_test_run_id ON communication_policy_audits (test_run_id);
CREATE INDEX ix_communication_policy_audits_profile_id ON communication_policy_audits (profile_id);

INSERT INTO communication_profiles (
    slug, name, description, system_instructions, required_fields, version,
    is_active, is_system, max_input_chars, max_auto_replies,
    max_duration_minutes, max_prompt_tokens, max_completion_tokens,
    max_estimated_cost_usd, updated_by
) VALUES
(
    'buyer', 'Закупщик',
    'Собирает полную сопоставимую котировку и обязательные документы.',
    'Профиль закупщика: продолжай диалог до получения цены и валюты, Incoterm, MOQ, грейда или чистоты, условий оплаты, срока и CoA либо TDS. Не обещай заказ, оплату или договор.',
    '["currency", "grade", "incoterm", "lead_time", "moq", "payment_terms", "price", "specification"]'::jsonb,
    1, TRUE, TRUE, 12000, 12, 10080, 60000, 12000, 10, 'система'
),
(
    'chemist', 'Химик-разработчик',
    'Уточняет идентичность, грейд и ориентир цены, затем передаёт закупке.',
    'Профиль химика-разработчика: сосредоточься на точной идентичности, грейде или чистоте и ориентире цены. После получения этих сведений нейтрально сообщи, что данные переданы коллегам по закупке. Не запрашивай оплату, логистику и полный пакет документов без явной инструкции оператора.',
    '["price", "currency", "grade"]'::jsonb,
    1, TRUE, TRUE, 8000, 8, 4320, 40000, 8000, 6, 'система'
);

INSERT INTO communication_profile_versions (
    profile_id, version, name, description, system_instructions,
    required_fields, max_input_chars, max_auto_replies, max_duration_minutes,
    max_prompt_tokens, max_completion_tokens, max_estimated_cost_usd, changed_by
)
SELECT id, version, name, description, system_instructions, required_fields,
       max_input_chars, max_auto_replies, max_duration_minutes,
       max_prompt_tokens, max_completion_tokens, max_estimated_cost_usd, 'система'
FROM communication_profiles
WHERE slug IN ('buyer', 'chemist');
