-- Решение человека по изготовителю в паспорте качества.
-- Автоматическая сверка на сокращённых названиях честно отвечает «нужна
-- ручная проверка»; без записи решения закупщик разбирается заново каждый
-- раз, а результат его разбора нигде не остаётся.
ALTER TABLE supplier_documents
    ADD COLUMN IF NOT EXISTS manufacturer_decision VARCHAR(16),
    ADD COLUMN IF NOT EXISTS manufacturer_decision_reason TEXT,
    ADD COLUMN IF NOT EXISTS manufacturer_decided_by_id INTEGER
        REFERENCES users(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS manufacturer_decided_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS ix_supplier_documents_manufacturer_decision
    ON supplier_documents (manufacturer_decision);
