-- Ручной черновик первого RFQ хранится отдельно от структурированных данных
-- запроса. NULL в обоих полях означает использование единого шаблона.
ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS rfq_subject_override VARCHAR(500);

ALTER TABLE rfqs
    ADD COLUMN IF NOT EXISTS rfq_body_override TEXT;
