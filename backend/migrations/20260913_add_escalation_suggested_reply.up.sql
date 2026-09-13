ALTER TABLE escalations
    ADD COLUMN IF NOT EXISTS suggested_reply TEXT;

UPDATE escalations
SET suggested_reply =
    'Thank you for your message. Your question requires internal review. We will get back to you shortly.'
WHERE communication_id IS NOT NULL
  AND suggested_reply IS NULL
  AND (
      note ILIKE 'Автоответ остановлен:%'
      OR note ILIKE 'Авторазбор позиции остановлен:%'
      OR note ILIKE 'Автоматическая обработка WhatsApp остановлена:%'
  );
