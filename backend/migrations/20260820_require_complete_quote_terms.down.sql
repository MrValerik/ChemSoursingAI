UPDATE communication_test_messages message
SET content = audit.previous_demo_message_content
FROM quote_terms_completeness_migration_20260820 audit
WHERE message.id = audit.demo_message_id;

UPDATE quotations quotation
SET is_complete = audit.previous_is_complete,
    currency = audit.previous_currency,
    grade = audit.previous_grade,
    payment_terms = audit.previous_payment_terms,
    lead_time = audit.previous_lead_time,
    field_confidence = audit.previous_field_confidence
FROM quote_terms_completeness_migration_20260820 audit
WHERE quotation.id = audit.quotation_id;

UPDATE communication_test_runs run
SET status = audit.previous_run_status
FROM quote_terms_completeness_migration_20260820 audit
WHERE run.id = audit.run_id
  AND audit.previous_run_status IS NOT NULL;

DROP TABLE quote_terms_completeness_migration_20260820;
