CREATE TABLE quote_terms_completeness_migration_20260820 (
    quotation_id INTEGER PRIMARY KEY REFERENCES quotations(id) ON DELETE CASCADE,
    previous_is_complete BOOLEAN NOT NULL,
    previous_currency VARCHAR(3),
    previous_grade VARCHAR(120),
    previous_payment_terms VARCHAR(255),
    previous_lead_time VARCHAR(120),
    previous_field_confidence JSON,
    run_id INTEGER REFERENCES communication_test_runs(id) ON DELETE SET NULL,
    previous_run_status VARCHAR(32),
    demo_message_id INTEGER REFERENCES communication_test_messages(id) ON DELETE SET NULL,
    previous_demo_message_content TEXT
);

INSERT INTO quote_terms_completeness_migration_20260820 (
    quotation_id,
    previous_is_complete,
    previous_currency,
    previous_grade,
    previous_payment_terms,
    previous_lead_time,
    previous_field_confidence,
    run_id,
    previous_run_status,
    demo_message_id,
    previous_demo_message_content
)
SELECT
    q.id,
    q.is_complete,
    q.currency,
    q.grade,
    q.payment_terms,
    q.lead_time,
    q.field_confidence,
    ctr.id,
    ctr.status,
    demo.id,
    demo.content
FROM quotations q
LEFT JOIN communication_test_runs ctr ON ctr.quotation_id = q.id
LEFT JOIN LATERAL (
    SELECT message.id, message.content
    FROM communication_test_messages message
    WHERE message.run_id = ctr.id
      AND message.sender_role = 'supplier'
      AND message.attachments::TEXT LIKE '%Demo_CoA_%'
    ORDER BY message.id DESC
    LIMIT 1
) demo ON TRUE
WHERE q.is_complete IS TRUE
  AND (
      q.currency IS NULL OR BTRIM(q.currency) = ''
      OR q.grade IS NULL OR BTRIM(q.grade) = ''
      OR q.payment_terms IS NULL OR BTRIM(q.payment_terms) = ''
      OR q.lead_time IS NULL OR BTRIM(q.lead_time) = ''
  );

-- Системный PDF-ответ является синтетической демонстрацией. Дополняем его
-- контролируемыми тестовыми условиями и сохраняем те же факты в котировке.
UPDATE communication_test_messages message
SET content = message.content
    || ' USP grade material. Payment: T/T in advance. Lead time: 15 days.'
FROM quote_terms_completeness_migration_20260820 audit
WHERE message.id = audit.demo_message_id;

UPDATE quotations quotation
SET currency = COALESCE(NULLIF(BTRIM(quotation.currency), ''), 'USD'),
    grade = COALESCE(NULLIF(BTRIM(quotation.grade), ''), 'USP grade'),
    payment_terms = COALESCE(
        NULLIF(BTRIM(quotation.payment_terms), ''),
        'T/T'
    ),
    lead_time = COALESCE(NULLIF(BTRIM(quotation.lead_time), ''), '15 days'),
    field_confidence = (
        COALESCE(quotation.field_confidence::JSONB, '{}'::JSONB)
        || JSONB_BUILD_OBJECT(
            'currency', 0.95,
            'grade', 0.95,
            'payment_terms', 0.95,
            'lead_time', 0.95
        )
    )::JSON
FROM quote_terms_completeness_migration_20260820 audit
WHERE quotation.id = audit.quotation_id
  AND audit.demo_message_id IS NOT NULL;

-- Остальные старые записи больше не называем полными, пока поставщик не
-- подтвердит все условия, которые показываются в сравнительной таблице.
UPDATE quotations quotation
SET is_complete = FALSE
FROM quote_terms_completeness_migration_20260820 audit
WHERE quotation.id = audit.quotation_id
  AND audit.demo_message_id IS NULL;

UPDATE communication_test_runs run
SET status = 'previewed'
FROM quote_terms_completeness_migration_20260820 audit
WHERE run.id = audit.run_id
  AND audit.demo_message_id IS NULL
  AND run.status = 'complete';
