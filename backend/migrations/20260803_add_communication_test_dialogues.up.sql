ALTER TABLE communication_test_runs
    ADD COLUMN procurement_context TEXT;

UPDATE communication_test_runs
SET procurement_context = customer_message
WHERE procurement_context IS NULL;

ALTER TABLE communication_test_runs
    ALTER COLUMN procurement_context SET NOT NULL;

ALTER TABLE communication_test_runs
    ADD COLUMN subject VARCHAR(998) NOT NULL DEFAULT 'Тест ChemSource AI';

CREATE TABLE communication_test_messages (
    id SERIAL PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES communication_test_runs(id) ON DELETE CASCADE,
    sender_role VARCHAR(32) NOT NULL,
    content TEXT NOT NULL,
    delivery_status VARCHAR(32) NOT NULL DEFAULT 'previewed',
    provider_message_id VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX ix_communication_test_messages_run_id
    ON communication_test_messages (run_id);
CREATE INDEX ix_communication_test_messages_sender_role
    ON communication_test_messages (sender_role);

-- Старые одноходовые тесты остаются доступны в новом представлении истории.
INSERT INTO communication_test_messages (
    run_id,
    sender_role,
    content,
    delivery_status,
    created_at,
    updated_at
)
SELECT
    id,
    'supplier',
    customer_message,
    'received',
    created_at,
    updated_at
FROM communication_test_runs;

INSERT INTO communication_test_messages (
    run_id,
    sender_role,
    content,
    delivery_status,
    provider_message_id,
    created_at,
    updated_at
)
SELECT
    id,
    'assistant',
    generated_reply,
    CASE WHEN status = 'sent' THEN 'sent' ELSE 'previewed' END,
    provider_message_id,
    created_at,
    updated_at
FROM communication_test_runs
WHERE generated_reply IS NOT NULL;
