ALTER TABLE quotations
    ADD COLUMN source_communication_id INTEGER
    REFERENCES communications(id) ON DELETE SET NULL;

CREATE INDEX ix_quotations_source_communication_id
    ON quotations (source_communication_id);

-- Старый email workflow создавал котировку в той же транзакции и с тем же
-- server timestamp, что и входящее сообщение. Это даёт однозначный безопасный
-- backfill без анализа текста или догадок по поставщику.
UPDATE quotations AS q
SET source_communication_id = c.id
FROM communications AS c
WHERE q.source_communication_id IS NULL
  AND q.rfq_id = c.rfq_id
  AND q.created_at = c.created_at
  AND c.direction = 'INBOUND'
  AND c.channel = 'EMAIL';

-- Адрес мог быть подтверждён уже после создания котировки. Коммуникации к этому
-- моменту перепривязаны, поэтому переносим только недостающую связь менеджера.
UPDATE quotations AS q
SET manager_id = c.manager_id
FROM communications AS c
WHERE q.source_communication_id = c.id
  AND q.manager_id IS NULL
  AND c.manager_id IS NOT NULL;
