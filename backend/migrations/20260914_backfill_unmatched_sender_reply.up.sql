UPDATE escalations
SET suggested_reply =
    'Thank you for your message. Your question requires internal review. We will get back to you shortly.'
WHERE communication_id IS NOT NULL
  AND suggested_reply IS NULL
  AND note ILIKE 'Отправитель первого письма не сопоставлен с ранее выбранным поставщиком.%';
